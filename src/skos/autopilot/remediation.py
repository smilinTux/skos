"""Gated remediation seam (ep-autopilot-ops: "Remediation seam").

The single boundary through which the ops executor is allowed to touch a live
host. It turns a proposed ssh_exec/ansible remediation into exactly one of:

  - ``deny``    nothing runs. The default for anything not explicitly
                allowlisted by a runbook, and a HARD stop for anything that
                would touch secret/kms material.
  - ``propose`` a destructive action is never executed here; it is routed to
                itil_change_propose (an RFC / CAB review) instead.
  - ``dry-run`` the action IS allowlisted and non-destructive, but nothing is
                applied: the gate returns the exact argv it *would* run. This is
                the default even for an allowed action.
  - ``apply``   the confined argv actually ran inside the Docker sandbox. Only
                reachable when the caller passes explicit approval
                (``dry_run=False``) AND policy is open (``live_execution=True``).
                Any other combination fails closed.

Safety model mirrors autopilot: deny-by-default, allowlisted per runbook, never
touches secrets/kms, live_execution off by default. The gate is the ONLY path
to ``apply``; every other entrypoint returns a plan or an escalation. Execution,
when it happens, goes through the same confined ``Sandbox`` the engineering
executor uses (secrets confined by absence, egress limited to the target host
via the allowlist proxy).
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

from .claude_code import HarnessUnavailable
from .sandbox import LaunchSpec, Sandbox

# Substrings that mean the action would touch secret / KMS material. Matching
# ANY of these is a hard deny (not a propose): the remediation seam never
# handles secret or key material, full stop (card: "never touches secrets/kms").
SECRET_MARKERS = (
    "kms", "skvault", "vault", "capauth", "secret", "password", "passwd",
    "id_rsa", "id_ed25519", "private_key", "privkey", ".ssh/", ".gnupg",
    ".skcapstone/agents", "keyring", "token", "credential", "--ask-vault",
)

# Verbs that make an action destructive even when a runbook forgot to flag it.
# A destructive action is never executed by the seam; it is routed to
# itil_change_propose for review (card: "destructive ops route to
# itil_change_propose").
DESTRUCTIVE_MARKERS = (
    "rm ", "rm -", "rmdir", "mkfs", "dd ", "shred", "truncate", "> /", ":> ",
    "reboot", "shutdown", "halt", "poweroff", "init 0", "init 6",
    "drop table", "drop database", "delete from", "truncate table",
    "systemctl stop", "systemctl disable", "systemctl mask",
    "kill -9", "killall", "pkill", "iptables -f", "userdel", "groupdel",
    "parted", "wipefs", "fdisk", "mv /",
)

_KINDS = ("ssh_exec", "ansible")


@dataclass
class RemediationAction:
    """One proposed remediation step. ``command`` is the shell command for
    ``ssh_exec`` or the playbook path for ``ansible``; ``host`` is both the
    target and the sole egress the sandbox proxy will allow."""

    kind: str
    command: str
    host: str
    args: list[str] = field(default_factory=list)   # extra ansible args
    destructive: bool | None = None                 # None -> infer from markers

    def is_destructive(self) -> bool:
        if self.destructive is not None:
            return self.destructive
        low = self.command.lower()
        return any(m in low for m in DESTRUCTIVE_MARKERS)

    def touches_secrets(self) -> bool:
        blob = " ".join([self.command, self.host, *self.args]).lower()
        return any(m in blob for m in SECRET_MARKERS)


@dataclass
class AllowRule:
    """One allowlist entry of a runbook. An action is permitted only when a rule
    matches its kind, its command (fnmatch glob) and, if the rule pins hosts,
    its host. A rule may also DECLARE the action destructive so a benign-looking
    command still routes to change-propose."""

    kind: str
    command: str                                    # fnmatch glob vs action.command
    hosts: list[str] = field(default_factory=list)  # empty -> any host
    destructive: bool = False

    def matches(self, action: RemediationAction) -> bool:
        if self.kind != action.kind:
            return False
        if not fnmatch.fnmatch(action.command, self.command):
            return False
        if self.hosts and action.host not in self.hosts:
            return False
        return True


@dataclass
class Runbook:
    """A named allowlist. Deny-by-default: an action is allowed only if one of
    its rules matches. Loaded from KEDB/runbook config by the ops executor; kept
    a plain dataclass here so the gate has no I/O."""

    id: str
    allow: list[AllowRule] = field(default_factory=list)

    def rule_for(self, action: RemediationAction) -> AllowRule | None:
        for rule in self.allow:
            if rule.matches(action):
                return rule
        return None


@dataclass
class GateDecision:
    decision: str                                   # deny | propose | dry-run | apply
    reason: str
    action: RemediationAction
    argv: list[str] | None = None
    change_id: str | None = None
    result: dict | None = None

    @property
    def applied(self) -> bool:
        return self.decision == "apply"

    @property
    def blocked(self) -> bool:
        return self.decision == "deny"


def build_argv(action: RemediationAction) -> list[str]:
    """The exact argv the seam would run inside the sandbox for this action.
    Non-interactive by construction (no key material, no host-key prompts)."""
    if action.kind == "ssh_exec":
        return ["ssh", "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=accept-new",
                action.host, action.command]
    if action.kind == "ansible":
        return ["ansible-playbook", "-i", f"{action.host},",
                action.command, *action.args]
    raise ValueError(f"unknown remediation kind {action.kind!r}")


def _default_change_proposer(**kwargs):
    """Live routing of a destructive action to an ITIL change RFC. Lazily imports
    skcapstone so skos never hard-depends on it (same pattern as the engineering
    executor's board/config loaders)."""
    from skcapstone.itil import ITILManager
    from skcapstone.mcp_tools._helpers import _shared_root

    mgr = ITILManager(_shared_root())
    mgr.ensure_dirs()
    return mgr.propose_change(**kwargs)


class RemediationGate:
    """The confinement + approval gate. ``evaluate`` is a pure, side-effect-free
    classification (deny/propose/allow) usable without docker; ``apply`` drives
    the full path and is the only method that can reach the sandbox."""

    def __init__(self, sandbox: Sandbox | None = None, *,
                 live_execution: bool = False, change_proposer=None,
                 sandbox_image: str | None = None) -> None:
        self.sandbox = sandbox
        self.live_execution = live_execution
        self._change_proposer = change_proposer
        self.sandbox_image = sandbox_image or "sandbox-ops:1"

    # ---- pure classification: no side effects, no docker ----
    def evaluate(self, action: RemediationAction, runbook: Runbook) -> GateDecision:
        if action.kind not in _KINDS:
            return GateDecision("deny", f"unsupported kind {action.kind!r}", action)
        # HARD deny: secret/kms material is never in scope for the seam.
        if action.touches_secrets():
            return GateDecision(
                "deny", "action references secret/kms material (hard deny)", action)
        rule = runbook.rule_for(action)
        if rule is None:
            # deny-by-default: nothing runs unless a runbook rule allowlists it.
            return GateDecision(
                "deny",
                f"no rule in runbook {runbook.id!r} allowlists this action "
                f"(deny-by-default)", action)
        if action.is_destructive() or rule.destructive:
            # destructive -> never executed here; caller routes to change-propose.
            return GateDecision(
                "propose",
                "destructive action routed to itil_change_propose for review",
                action)
        return GateDecision(
            "dry-run", f"allowed by runbook {runbook.id!r}", action,
            argv=build_argv(action))

    # ---- full path: the only route to apply ----
    def apply(self, action: RemediationAction, runbook: Runbook, *,
              worktree: str, dry_run: bool = True) -> GateDecision:
        decision = self.evaluate(action, runbook)
        if decision.decision == "deny":
            return decision
        if decision.decision == "propose":
            change_id = self._propose(action, runbook)
            decision.change_id = change_id
            return decision

        argv = decision.argv or build_argv(action)
        if dry_run:
            # default even for an allowed action: approved-but-not-applied.
            return GateDecision(
                "dry-run",
                "dry-run (default): pass dry_run=False AND live_execution to apply",
                action, argv=argv)
        # explicit approval given (dry_run=False) — still gated on policy.
        if not self.live_execution:
            return GateDecision(
                "deny", "apply requested but live_execution is off (fail closed)",
                action, argv=argv)
        if self.sandbox is None:
            raise HarnessUnavailable("no sandbox configured; cannot apply (fail closed)")
        spec = LaunchSpec(
            name=f"remediate-{action.kind}", argv=argv, image=self.sandbox_image,
            worktree=worktree, auth_mounts=[], auth_env={},
            egress_hosts=[action.host])
        result = self.sandbox.spawn(spec, repo_remote_host=action.host)
        return GateDecision("apply", "applied in confined sandbox", action,
                            argv=argv, result=result)

    def _propose(self, action: RemediationAction, runbook: Runbook) -> str | None:
        proposer = self._change_proposer or _default_change_proposer
        change = proposer(
            title=f"[remediation:{runbook.id}] {action.kind} on {action.host}",
            change_type="normal",
            risk="high",
            rollback_plan="(operator to specify before CAB approval)",
            test_plan=f"re-check {action.host} health after the change",
            created_by="autopilot-remediation",
            tags=["autopilot", "remediation", f"runbook:{runbook.id}",
                  f"host:{action.host}"],
        )
        if change is None:
            return None
        if isinstance(change, dict):
            return change.get("id")
        return getattr(change, "id", None)
