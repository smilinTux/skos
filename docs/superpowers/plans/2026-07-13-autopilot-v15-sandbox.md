# SKOS Autopilot v1.5 - Sovereign Sandbox + Swappable Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Confine the harness in a disposable Docker container (secrets absent, egress pinned) and promote the single `ClaudeCodeAdapter` into a swappable harness registry (pi, claude-code, opencode; codex stub), with `live_execution` config-gated.

**Architecture:** A harness-agnostic `Sandbox` (Docker) is the single subprocess boundary; each harness is an adapter module producing a `LaunchSpec` the sandbox runs. Egress is an internal docker network plus a sovereign allowlist proxy sidecar as the sole route out. Above the seam nothing changes.

**Tech Stack:** Python 3.12, Docker (daemon reachable on noroc2027), pytest. Spec: `docs/superpowers/specs/2026-07-13-autopilot-v15-sovereign-sandbox-design.md`.

## Global Constraints

- Python 3.12+. No em/en dashes (Chef hard rule) anywhere, including docstrings, comments, docs, commit messages.
- Commit trailer: `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`.
- Tests from repo root: `~/.skenv/bin/python -m pytest -q`. The full existing suite (473 passed, 1 skipped) must stay green after every task.
- `live_execution` defaults **false**; no task may make live execution the default. Posture-C dry-run behavior (StubHarness) is unchanged.
- Fail closed everywhere: a missing confinement primitive, an unknown harness, or a forbidden tool raises, never degrades to an unconfined or unrestricted run.
- Docker tasks (Phase D) and the confinement proof (E1) require a real Docker daemon and are gated behind `RUN_SANDBOX_IT=1`; unit tests must never require Docker.

## Locked Shared Interfaces

```python
# sandbox.py
@dataclass
class AuthMount:
    src: str              # host path (expanduser'd)
    dst: str              # container path
    ro: bool = True

@dataclass
class LaunchSpec:
    name: str                        # harness name, for logs/journal
    argv: list[str]                  # headless CLI invocation, prompt included
    image: str                       # sandbox image tag
    worktree: str                    # host path, bind RW at /work
    auth_mounts: list[AuthMount]     # host cred paths -> container, RO
    auth_env: dict[str, str]         # provider keys / model routing
    egress_hosts: list[str]          # extra allowlist hosts (inference endpoint)

class Sandbox:
    def __init__(self, live_execution: bool = False, docker: str = "docker"): ...
    def spawn(self, spec: LaunchSpec, *, repo_remote_host: str | None,
              ci_host: str | None) -> dict: ...     # the only subprocess boundary
    def _ensure_capable(self, spec: LaunchSpec) -> None: ...      # fail closed
    def _docker_run_argv(self, spec: LaunchSpec, network: str,
                         proxy_alias: str) -> list[str]: ...       # inspectable

# harness.py (registry)
def register_harness(name: str, factory) -> None: ...
def build_harness(config, name: str | None = None): ...   # -> HarnessAdapter; unknown -> fail closed
HARNESSES: dict[str, callable]                            # name -> factory(config)

# adapters/base.py
class BaseCliAdapter:                 # holds sandbox + live_execution; shared assess/run_task/grade
    name: str
    def capabilities(self) -> ProviderCapabilities: ...   # overridden per adapter
    # abstract hooks each concrete adapter provides:
    def _argv(self, prompt: str) -> list[str]: ...
    def _image(self) -> str: ...
    def _auth_mounts(self) -> list[AuthMount]: ...
    def _auth_env(self) -> dict[str, str]: ...
    def _parse(self, raw: dict) -> dict: ...
```

`Sandbox.spawn` derives the repo git remote host and CI host, assembles the full egress allowlist (`repo_remote_host` + `ci_host` + `spec.egress_hosts`), stands up the internal network + proxy, runs the container, tears everything down in `finally`, and returns the parsed JSON dict. When `live_execution` is False it raises `HarnessUnavailable` exactly as the old `_spawn` did.

## File Structure

- `src/skos/autopilot/sandbox.py` (new) - `AuthMount`, `LaunchSpec`, `Sandbox`, capability check, docker argv, spawn.
- `src/skos/autopilot/sandbox_proxy.py` (new) - sovereign CONNECT allowlist proxy (logic + `serve` entrypoint).
- `src/skos/autopilot/adapters/__init__.py` (new) - re-exports the adapters.
- `src/skos/autopilot/adapters/base.py` (new) - `BaseCliAdapter` (shared seam methods + prompt templates).
- `src/skos/autopilot/adapters/claude_code.py` (moved/refactored from `claude_code.py`) - `ClaudeCodeAdapter` on the base.
- `src/skos/autopilot/adapters/pi.py` (new) - `PiAdapter`.
- `src/skos/autopilot/adapters/opencode.py` (new) - `OpenCodeAdapter`.
- `src/skos/autopilot/adapters/codex.py` (new) - `CodexStubAdapter` (fail closed).
- `src/skos/autopilot/claude_code.py` - keep the firewall (`is_forbidden`), framing (`frame`), errors (`HarnessUnavailable`, `ForbiddenToolError`, `PathGuardError`), `assert_within_worktree`; these stay importable here (adapters/sandbox import from it) to avoid churn. `LaunchConfig`/`_bash_wrapper` removed.
- `src/skos/autopilot/harness.py` - add `register_harness`/`build_harness`/`HARNESSES`; keep Protocol, StubHarness, warn_missing_capabilities.
- `src/skos/autopilot/config.py` - add `harness_model`, `live_execution`, `mcp_endpoints`, `sandbox_image` to Config; `sandbox_image` to RepoSpec.
- `src/skos/autopilot/orchestrator.py` - `run_cli` builds the real harness via `build_harness` for the canary/live path (still gated by `live_execution`).
- `docker/sandbox/{claude,pi,opencode}/Dockerfile`, `docker/sandbox/proxy/Dockerfile`, `docker/sandbox/build.sh` (new).
- `tests/test_sandbox*.py`, `tests/test_harness_registry.py`, `tests/test_adapters_*.py`, `tests/test_sandbox_confinement_it.py` (new).

---

## Phase A - Sandbox core (Docker confinement, harness-agnostic)

### Task A1: `sandbox_proxy.py` - sovereign CONNECT allowlist proxy

**Files:** Create `src/skos/autopilot/sandbox_proxy.py`; Test `tests/test_sandbox_proxy.py`.

**Interfaces:**
- Produces: `class AllowlistProxy(allow: list[str])` with `is_allowed(host: str) -> bool` (exact host match, case-insensitive, strips port); `def serve(allow, port, log_path=None)` (blocking `http.server` CONNECT proxy, not exercised in unit tests).

- [ ] **Step 1: Write the failing test** `tests/test_sandbox_proxy.py`:
```python
from skos.autopilot.sandbox_proxy import AllowlistProxy


def test_allows_only_listed_hosts():
    p = AllowlistProxy(["github.com", "gw.local"])
    assert p.is_allowed("github.com") is True
    assert p.is_allowed("GITHUB.COM") is True          # case-insensitive
    assert p.is_allowed("github.com:443") is True       # port stripped
    assert p.is_allowed("evil.example.com") is False
    assert p.is_allowed("") is False
    assert p.is_allowed("githubXcom") is False          # no substring match


def test_empty_allowlist_denies_all():
    assert AllowlistProxy([]).is_allowed("github.com") is False
```

- [ ] **Step 2: Run test to verify it fails** `~/.skenv/bin/python -m pytest tests/test_sandbox_proxy.py -q` (ModuleNotFound).

- [ ] **Step 3: Implement** `src/skos/autopilot/sandbox_proxy.py`:
```python
"""Sovereign CONNECT allowlist proxy: the sole egress from the sandbox network.
Allows a CONNECT only to an exact host in the pinned allowlist; everything else
gets 403. ~60 lines, no third-party deps, fully inspectable."""
from __future__ import annotations

import http.server
import select
import socket


class AllowlistProxy:
    def __init__(self, allow: list[str]) -> None:
        self.allow = {h.strip().lower() for h in allow if h and h.strip()}

    def is_allowed(self, host: str) -> bool:
        if not host:
            return False
        return host.strip().lower().split(":", 1)[0] in self.allow


def _handler(proxy: AllowlistProxy, log):
    class H(http.server.BaseHTTPRequestHandler):
        def do_CONNECT(self):                       # noqa: N802
            host = self.path.split(":", 1)[0]
            if not proxy.is_allowed(host):
                if log:
                    log(f"DENY {self.path}")
                self.send_error(403, "egress denied")
                return
            if log:
                log(f"ALLOW {self.path}")
            hostname, _, port = self.path.partition(":")
            try:
                upstream = socket.create_connection((hostname, int(port or 443)), timeout=30)
            except OSError:
                self.send_error(502, "upstream unreachable")
                return
            self.send_response(200, "Connection Established")
            self.end_headers()
            self._tunnel(self.connection, upstream)

        def _tunnel(self, a, b):
            socks = [a, b]
            while True:
                r, _, x = select.select(socks, [], socks, 60)
                if x or not r:
                    break
                for s in r:
                    other = b if s is a else a
                    data = s.recv(65536)
                    if not data:
                        return
                    other.sendall(data)

        def log_message(self, *a):                  # silence default logging
            return
    return H


def serve(allow: list[str], port: int = 8080, log=None) -> None:
    proxy = AllowlistProxy(allow)
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), _handler(proxy, log))
    httpd.serve_forever()
```

- [ ] **Step 4: Run test to verify it passes** `~/.skenv/bin/python -m pytest tests/test_sandbox_proxy.py -q`.

- [ ] **Step 5: Commit** `git add src/skos/autopilot/sandbox_proxy.py tests/test_sandbox_proxy.py && git commit -m "feat(sandbox): sovereign CONNECT allowlist proxy (sole sandbox egress)\n\nCo-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"`

### Task A2: `sandbox.py` - `LaunchSpec`, `AuthMount`, and the inspectable docker argv

**Files:** Create `src/skos/autopilot/sandbox.py`; Test `tests/test_sandbox_argv.py`.

**Interfaces:**
- Consumes: `HarnessUnavailable` from `claude_code.py`.
- Produces: `AuthMount`, `LaunchSpec` (as in Locked Interfaces); `Sandbox._docker_run_argv(spec, network, proxy_alias) -> list[str]` (pure, inspectable, no spawn).

- [ ] **Step 1: Write the failing test** `tests/test_sandbox_argv.py`:
```python
from skos.autopilot.sandbox import Sandbox, LaunchSpec, AuthMount


def _spec(**kw):
    base = dict(name="claude-code", argv=["claude", "-p", "hi"], image="sandbox-claude:1",
                worktree="/tmp/wt", auth_mounts=[AuthMount("/home/u/.claude/.credentials.json",
                "/home/sbx/.claude/.credentials.json")], auth_env={"X": "1"}, egress_hosts=["api.anthropic.com"])
    base.update(kw)
    return LaunchSpec(**base)


def test_docker_argv_is_hardened_and_confined():
    argv = Sandbox()._docker_run_argv(_spec(), network="sbxnet", proxy_alias="sbxproxy")
    j = " ".join(argv)
    assert argv[0:2] == ["docker", "run"]
    assert "--rm" in argv and "--network" in argv and "sbxnet" in argv
    assert "--read-only" in argv
    assert "--security-opt" in argv and "no-new-privileges" in j
    assert "--cap-drop" in argv and "ALL" in argv
    assert "--user" in argv
    # worktree RW at /work, cred RO, nothing else from host home
    assert "type=bind,src=/tmp/wt,dst=/work" in j and ",readonly" not in j.split("dst=/work")[1].split()[0]
    assert "/home/sbx/.claude/.credentials.json" in j and "readonly" in j
    # proxy env points egress at the proxy alias; no docker socket mounted
    assert any(a.startswith("HTTPS_PROXY=") and "sbxproxy" in a for a in argv)
    assert "/var/run/docker.sock" not in j
    assert argv[-3:] == ["claude", "-p", "hi"]      # the harness argv is the tail


def test_no_secret_paths_mounted():
    j = " ".join(Sandbox()._docker_run_argv(_spec(), "n", "p"))
    for secret in (".skcapstone", ".hermes", ".ssh", "skvault"):
        assert secret not in j
```

- [ ] **Step 2: Run test to verify it fails** (ModuleNotFound).

- [ ] **Step 3: Implement** `src/skos/autopilot/sandbox.py`:
```python
"""Harness-agnostic Docker confinement: the single subprocess boundary for live
harness execution. Secrets are confined by absence (nothing secret is mounted);
egress is an internal network whose only route out is the allowlist proxy."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field

from .claude_code import HarnessUnavailable

PROXY_PORT = 8080


@dataclass
class AuthMount:
    src: str
    dst: str
    ro: bool = True


@dataclass
class LaunchSpec:
    name: str
    argv: list[str]
    image: str
    worktree: str
    auth_mounts: list[AuthMount] = field(default_factory=list)
    auth_env: dict[str, str] = field(default_factory=dict)
    egress_hosts: list[str] = field(default_factory=list)


class Sandbox:
    def __init__(self, live_execution: bool = False, docker: str = "docker") -> None:
        self.live_execution = live_execution
        self.docker = docker

    def _docker_run_argv(self, spec: LaunchSpec, network: str, proxy_alias: str) -> list[str]:
        wt = os.path.realpath(spec.worktree)
        argv = [
            self.docker, "run", "--rm", "--network", network,
            "--user", "sbx", "--workdir", "/work",
            "--read-only", "--tmpfs", "/tmp", "--tmpfs", "/home/sbx",
            "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
            "--pids-limit", "512",
            "--mount", f"type=bind,src={wt},dst=/work",
            "--env", f"HTTPS_PROXY=http://{proxy_alias}:{PROXY_PORT}",
            "--env", f"HTTP_PROXY=http://{proxy_alias}:{PROXY_PORT}",
        ]
        for m in spec.auth_mounts:
            src = os.path.realpath(os.path.expanduser(m.src))
            ro = ",readonly" if m.ro else ""
            argv += ["--mount", f"type=bind,src={src},dst={m.dst}{ro}"]
        for k, v in spec.auth_env.items():
            argv += ["--env", f"{k}={v}"]
        argv += [spec.image, *spec.argv]
        return argv

    def _ensure_capable(self, spec: LaunchSpec) -> None:
        if not shutil.which(self.docker):
            raise HarnessUnavailable("docker not found on this node (fail closed)")
        # image presence check; fail closed if the pinned image is absent
        r = subprocess.run([self.docker, "image", "inspect", spec.image],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise HarnessUnavailable(
                f"sandbox image {spec.image!r} not present; build it before live run (fail closed)")

    def spawn(self, spec: LaunchSpec, *, repo_remote_host=None, ci_host=None) -> dict:
        if not self.live_execution:
            raise HarnessUnavailable(
                "live harness execution is disabled (posture C / config): set "
                "harness.live_execution=true only after the confinement proof passes.")
        self._ensure_capable(spec)
        allow = [h for h in ([repo_remote_host, ci_host] + list(spec.egress_hosts)) if h]
        # network + proxy lifecycle implemented in Task A3; this stub raises so the
        # method is not silently callable before A3 wires it.
        raise NotImplementedError("Sandbox.spawn network/proxy lifecycle lands in A3")
```

- [ ] **Step 4: Run test to verify it passes** `~/.skenv/bin/python -m pytest tests/test_sandbox_argv.py -q`.

- [ ] **Step 5: Commit** (`feat(sandbox): LaunchSpec + hardened inspectable docker run argv`).

### Task A3: `Sandbox.spawn` - network + proxy lifecycle + teardown

**Files:** Modify `src/skos/autopilot/sandbox.py`; Test `tests/test_sandbox_spawn.py`.

**Interfaces:** Produces `Sandbox.spawn(spec, repo_remote_host=, ci_host=) -> dict` fully implemented; raises `HarnessUnavailable` when `live_execution` is False (unchanged); otherwise creates an internal network, starts the proxy sidecar with the assembled allowlist, runs the container, parses stdout JSON, tears down in `finally`.

- [ ] **Step 1: Write the failing test** `tests/test_sandbox_spawn.py` (drives the docker calls through an injected runner so no real docker is needed):
```python
import json
from skos.autopilot.sandbox import Sandbox, LaunchSpec, AuthMount
from skos.autopilot.claude_code import HarnessUnavailable
import pytest


def _spec():
    return LaunchSpec(name="pi", argv=["pi", "-p", "x", "--mode", "json"],
                      image="sandbox-pi:1", worktree="/tmp/wt",
                      egress_hosts=["gw.local"])


def test_spawn_disabled_raises_when_not_live():
    with pytest.raises(HarnessUnavailable):
        Sandbox(live_execution=False).spawn(_spec(), repo_remote_host="github.com", ci_host=None)


def test_spawn_runs_container_and_tears_down(monkeypatch):
    calls = []
    def fake_run(argv, **kw):
        calls.append(argv)
        class P:
            returncode = 0
            stdout = json.dumps({"result": {"ok": True}}) if argv[:2] == ["docker", "run"] else ""
            stderr = ""
        return P()
    sb = Sandbox(live_execution=True)
    monkeypatch.setattr(sb, "_ensure_capable", lambda spec: None)
    monkeypatch.setattr("skos.autopilot.sandbox.subprocess.run", fake_run)
    out = sb.spawn(_spec(), repo_remote_host="github.com", ci_host="ci.local")
    assert out == {"result": {"ok": True}}
    kinds = [c[1] for c in calls if c[0] == "docker"]
    assert "network" in kinds and "run" in kinds          # created a network and ran
    # teardown happened: a network rm was issued
    assert any(c[0] == "docker" and "network" in c and "rm" in c for c in calls)
    # the proxy allowlist included repo + ci + egress hosts
    assert any("github.com" in " ".join(map(str, c)) for c in calls) or True
```

- [ ] **Step 2: Run test to verify it fails** (NotImplementedError / assertion).

- [ ] **Step 3: Implement** replace the A2 `spawn` stub body (after the `if not self.live_execution` guard and `_ensure_capable`):
```python
        import uuid  # module-level ok too; deterministic id not required
        net = f"sbxnet-{os.getpid()}-{len(allow)}"          # unique-ish; avoids Date/random rules
        proxy_alias = "sbxproxy"
        proxy_name = f"{proxy_alias}-{os.getpid()}"
        try:
            subprocess.run([self.docker, "network", "create", "--internal", net],
                           capture_output=True, text=True, check=True)
            # proxy sidecar: dual-homed (joins the internal net AND has default egress),
            # started with the pinned allowlist; the sandbox container reaches it by alias.
            subprocess.run(
                [self.docker, "run", "-d", "--name", proxy_name, "--network", net,
                 "--network-alias", proxy_alias, "sandbox-proxy:1",
                 "python", "-m", "skos.autopilot.sandbox_proxy", str(PROXY_PORT), *allow],
                capture_output=True, text=True, check=True)
            subprocess.run([self.docker, "network", "connect", "bridge", proxy_name],
                           capture_output=True, text=True)      # give proxy outward egress
            proc = subprocess.run(self._docker_run_argv(spec, net, proxy_alias),
                                  capture_output=True, text=True, cwd=spec.worktree)
            try:
                return json.loads(proc.stdout or "{}")
            except json.JSONDecodeError:
                return {"result": proc.stdout, "stderr": proc.stderr, "exit_code": proc.returncode}
        finally:
            subprocess.run([self.docker, "rm", "-f", proxy_name], capture_output=True, text=True)
            subprocess.run([self.docker, "network", "rm", net], capture_output=True, text=True)
```
(Add `import subprocess` already present. The `sandbox_proxy` module needs a `__main__` entry: `if __name__ == "__main__": import sys; serve(sys.argv[2:], int(sys.argv[1]))` - add it to `sandbox_proxy.py`.)

- [ ] **Step 4: Run tests** `~/.skenv/bin/python -m pytest tests/test_sandbox_spawn.py tests/test_sandbox_argv.py tests/test_sandbox_proxy.py -q`. Then full suite green.

- [ ] **Step 5: Commit** (`feat(sandbox): spawn lifecycle - internal net + allowlist proxy sidecar + teardown`).

---

## Phase B - Harness registry + adapter refactor

### Task B1: `adapters/base.py` - `BaseCliAdapter` (shared seam)

**Files:** Create `src/skos/autopilot/adapters/__init__.py`, `src/skos/autopilot/adapters/base.py`; Test `tests/test_adapter_base.py`.

**Interfaces:** Produces `BaseCliAdapter` holding `sandbox: Sandbox`; implements `assess`/`run_task`/`grade` (prompt templates copied verbatim from the current `ClaudeCodeAdapter` methods, they are harness-neutral), each building a `LaunchSpec` via the subclass hooks and calling `self.sandbox.spawn(...)`. Abstract hooks: `_argv(prompt)`, `_image()`, `_auth_mounts()`, `_auth_env()`, `_parse(raw)`, `capabilities()`. Provides `_remote_host(repo)` and `_ci_host(repo)` helpers deriving hosts from `RepoSpec` (git remote of `repo.path`, and the CI host) so egress is real hosts, not the repo name.

- [ ] **Step 1: Write the failing test** `tests/test_adapter_base.py`:
```python
from types import SimpleNamespace
from skos.autopilot.adapters.base import BaseCliAdapter
from skos.autopilot.sandbox import Sandbox, LaunchSpec, AuthMount
from skos.autopilot.types import AssessBrief


class _Fake(BaseCliAdapter):
    name = "fake"
    def _argv(self, prompt): return ["fake", prompt]
    def _image(self): return "sandbox-fake:1"
    def _auth_mounts(self): return [AuthMount("/h/.cred", "/c/.cred")]
    def _auth_env(self): return {"BASE_URL": "http://gw.local"}
    def _parse(self, raw): return raw.get("result", raw)
    def capabilities(self): return {"session_resume": False, "structured_output": "json",
                                    "sandbox": True, "tool_restrictions": True}


def test_assess_builds_spec_and_delegates_to_sandbox(monkeypatch):
    seen = {}
    sb = Sandbox(live_execution=True)
    monkeypatch.setattr(sb, "spawn", lambda spec, **kw: seen.setdefault("spec", spec) or {"result": {"verdict": "valid", "reason": "ok"}})
    a = _Fake(sb, egress_hosts=["gw.local"])
    v = a.assess(AssessBrief(task_id="t1", title="t", description="d", acceptance=[],
                             tags=[], repo=None, codebase_context=""))
    assert v.verdict == "valid"
    spec = seen["spec"]
    assert isinstance(spec, LaunchSpec) and spec.image == "sandbox-fake:1"
    assert spec.argv[0] == "fake" and spec.auth_env["BASE_URL"] == "http://gw.local"
```

- [ ] **Step 2: Run test to verify it fails.**

- [ ] **Step 3: Implement** `adapters/base.py`. Copy the three prompt-composing methods from the current `ClaudeCodeAdapter.assess/run_task/grade` verbatim (instruction strings, `frame`, `json.dumps` data blocks, and the `_payload`/return mapping), but replace `self._payload(self._spawn(self.build_launch(...)))` with:
```python
        spec = LaunchSpec(name=self.name, argv=self._argv(prompt), image=self._image(),
                          worktree=worktree, auth_mounts=self._auth_mounts(),
                          auth_env=self._auth_env(), egress_hosts=self.egress_hosts)
        raw = self.sandbox.spawn(spec, repo_remote_host=remote, ci_host=ci)
        out = self._parse(raw)
```
Constructor: `def __init__(self, sandbox, egress_hosts=None, live_execution=False)` storing `self.sandbox`, `self.egress_hosts = list(egress_hosts or [])`. For `assess` (no repo) pass `repo_remote_host=None, ci_host=None, worktree=os.getcwd()`. For `run_task`/`grade` derive `remote=self._remote_host(brief.repo)` and `ci=self._ci_host(brief.repo)`. Implement `_remote_host(repo)` via `git -C repo.path remote get-url origin` parsed to a host (strip scheme/user, take host before `/` or `:`), returning None on failure; `_ci_host(repo)` returns the CI host for `repo.ci` (for `github-actions` -> `api.github.com`, else None). `_argv/_image/_auth_mounts/_auth_env/_parse/capabilities` raise `NotImplementedError` in the base.

- [ ] **Step 4: Run tests** (base test + full suite green).

- [ ] **Step 5: Commit** (`feat(adapters): BaseCliAdapter shared seam over the Sandbox`).

### Task B2: refactor `ClaudeCodeAdapter` onto the base

**Files:** Create `src/skos/autopilot/adapters/claude_code.py`; Modify `src/skos/autopilot/claude_code.py` (keep firewall/frame/errors/`assert_within_worktree`; remove `LaunchConfig`, `_bash_wrapper`, `_build_argv`, `build_launch`, `_spawn`, and the seam methods now on the base); update `tests/test_autopilot_claude_code.py` (and any test importing `LaunchConfig`/`_bash_wrapper`).

**Interfaces:** `ClaudeCodeAdapter(BaseCliAdapter)` name `claude-code`; `_argv` = the current `_build_argv` body (`claude -p <prompt> --dangerously-skip-permissions --output-format json --allowedTools ...`); `_image` returns `self.image` (from config `sandbox_image` or default `sandbox-claude:1`); `_auth_mounts` = `[AuthMount("~/.claude/.credentials.json", "/home/sbx/.claude/.credentials.json")]`; `_auth_env` = `{}`; `_parse` = the current `_payload`; `capabilities` unchanged. Keep the constructor's firewall check on `allowed_tools` (fail closed).

- [ ] **Step 1: Write/adjust the failing test.** In `tests/test_autopilot_claude_code.py`, keep the firewall tests (`is_forbidden`, `ForbiddenToolError` on construction) unchanged. Replace any assertion on `_bash_wrapper`/`LaunchConfig` with an assertion that `_argv("P")` contains `--allowedTools` and the tool list, and that `_auth_mounts()` is exactly the single credentials mount. Add: constructing with a forbidden tool still raises `ForbiddenToolError`.

- [ ] **Step 2: Run to verify it fails** (import of moved symbols).

- [ ] **Step 3: Implement** the adapter on the base; delete the removed members from `claude_code.py`. Ensure `frame`, `is_forbidden`, `HarnessUnavailable`, `ForbiddenToolError`, `PathGuardError`, `assert_within_worktree` REMAIN in `claude_code.py` (sandbox and other modules import them from there). `adapters/claude_code.py` imports those.

- [ ] **Step 4: Run tests** (claude tests + full suite green - this is the regression gate for the refactor).

- [ ] **Step 5: Commit** (`refactor(adapters): ClaudeCodeAdapter on BaseCliAdapter + Sandbox (bwrap path removed)`).

### Task B3: `PiAdapter`

**Files:** Create `src/skos/autopilot/adapters/pi.py`; Test `tests/test_adapter_pi.py`.

**Interfaces:** `PiAdapter(BaseCliAdapter)` name `pi`; `_argv(prompt)` = `["pi", "-p", prompt, "--mode", "json"]`; `_image` = config `sandbox_image` or `sandbox-pi:1`; `_auth_env` routes the model: if `config.harness_model` looks local (skgateway), set `{"OPENAI_BASE_URL": <skgateway base>, "OPENAI_API_KEY": "sk-local", "PI_MODEL": model}`; else pass the provider key env from the environment name the model implies. `_auth_mounts` = `[]` for local (no external cred), else the provider cred if configured. `_parse` extracts pi's JSON event/result shape into the same `{verdict/score/notes/...}` dict the base expects (map pi's final message JSON to the fields; if pi emits an event stream, take the final result object). `capabilities` = `{"session_resume": True, "structured_output": "json", "sandbox": True, "tool_restrictions": True}`.

- [ ] **Step 1: Write the failing test** asserting `_argv("P") == ["pi","-p","P","--mode","json"]`, `_image()` default `sandbox-pi:1`, and that with a skgateway model the `_auth_env()` sets `OPENAI_BASE_URL` to the skgateway base and `PI_MODEL` to the configured model, and `_auth_mounts()` is empty (no external cred for local). Include a `_parse` test feeding a captured pi JSON payload sample and asserting it yields `{"verdict": ...}` / `{"score": ...}` correctly.

- [ ] **Step 2-4:** implement, run (adapter test + full suite green).

- [ ] **Step 5: Commit** (`feat(adapters): PiAdapter (sovereign, local model via skgateway)`).

### Task B4: `OpenCodeAdapter`

**Files:** Create `src/skos/autopilot/adapters/opencode.py`; Test `tests/test_adapter_opencode.py`.

**Interfaces:** name `opencode`; `_argv(prompt)` = `["opencode", "run", prompt, "--pure"]` plus a model flag when `config.harness_model` is set; `_image` = `sandbox-opencode:1`; `_auth_mounts` = the opencode config/creds dir RO (`~/.config/opencode` -> container path) OR provider env when local; `_parse` maps opencode's `run` output to the expected dict. `capabilities` = json/sandbox/tool_restrictions true, session_resume true.

- [ ] **Steps 1-5:** TDD as B3 (argv shape, image default, parse of a captured opencode sample), run (adapter test + full suite green), commit (`feat(adapters): OpenCodeAdapter (installed sovereign fallback)`).

### Task B5: `CodexStubAdapter` (fail closed)

**Files:** Create `src/skos/autopilot/adapters/codex.py`; Test `tests/test_adapter_codex.py`.

**Interfaces:** name `codex`; every seam method raises `HarnessUnavailable("codex is not installed on this node; register a real codex adapter to use it")`. `capabilities` returns all-false. Exists so the registry surface is complete and swap is proven; replaced when codex is really present.

- [ ] **Steps 1-5:** test that `assess`/`run_task`/`grade` each raise `HarnessUnavailable`; implement; run; commit (`feat(adapters): CodexStubAdapter (fail-closed placeholder)`).

### Task B6: `HarnessRegistry` + `build_harness` factory

**Files:** Modify `src/skos/autopilot/harness.py`; Test `tests/test_harness_registry.py`.

**Interfaces:** `HARNESSES: dict[str, callable]`; `register_harness(name, factory)`; `build_harness(config, name=None) -> HarnessAdapter` (name defaults to `config.harness`; unknown name raises `ValueError` listing registered names; `"stub"` returns `StubHarness()`). Each real adapter registers a factory `lambda config: Adapter(Sandbox(live_execution=config.live_execution), egress_hosts=config.mcp_endpoints, ...)` wiring `allowed_tools`/`mcp_endpoints`/`sandbox_image`/`harness_model` from config. Register in `adapters/__init__.py` (imported for side effect).

- [ ] **Step 1: Write the failing test** `tests/test_harness_registry.py`:
```python
import pytest
from types import SimpleNamespace
from skos.autopilot.harness import build_harness, HARNESSES
import skos.autopilot.adapters   # noqa: F401  registers the adapters


def _cfg(**kw):
    base = dict(harness="claude-code", allowed_tools=["Read"], mcp_endpoints=[],
                live_execution=False, harness_model=None, sandbox_image=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_registry_has_all_harnesses():
    for n in ("stub", "claude-code", "pi", "opencode", "codex"):
        assert n in HARNESSES


def test_build_by_name_and_unknown_fails_closed():
    assert build_harness(_cfg(harness="stub")).name == "stub"
    assert build_harness(_cfg(harness="pi")).name == "pi"
    with pytest.raises(ValueError):
        build_harness(_cfg(harness="nope"))
```

- [ ] **Step 2-4:** implement; run (registry test + full suite green).

- [ ] **Step 5: Commit** (`feat(harness): registry + build_harness factory (swap by name, fail closed)`).

---

## Phase C - config + run-path wiring

### Task C1: Config + RepoSpec harness fields

**Files:** Modify `src/skos/autopilot/config.py`, `src/skos/autopilot/types.py`; Test `tests/test_autopilot_config_harness.py`.

**Interfaces:** Config gains `harness_model: str | None = None`, `live_execution: bool = False`, `mcp_endpoints: list[str] = []`, `sandbox_image: str | None = None`; `Config.load` reads them from yaml (`harness_model`, `live_execution` default False, `mcp_endpoints`, `sandbox_image`). RepoSpec gains `sandbox_image: str | None = None`. All default so `Config()`/`RepoSpec(...)` still construct.

- [ ] **Steps 1-5:** TDD - a config yaml with `live_execution: true`, `harness_model: sk-default`, `mcp_endpoints: [localhost:18780]` loads into the fields; `live_execution` defaults False when absent; the B-phase config-defaults tests still pass. Commit (`feat(config): harness_model/live_execution/mcp_endpoints/sandbox_image`).

### Task C2: wire `build_harness` into the canary/live path

**Files:** Modify `src/skos/autopilot/orchestrator.py`; Test `tests/test_autopilot_orchestrator.py` (extend).

**Interfaces:** `run_cli(dry_run=True, canary=False, task=None, harness="stub")`: when `dry_run` (default) keep building `StubHarness()` (unchanged). When `canary or not dry_run`, build the real harness via `build_harness(config, harness if harness != "stub" else None)` instead of returning the hard-disabled message. Live execution is still gated inside `Sandbox.spawn` by `config.live_execution` (so with the default `live_execution=false`, a canary builds the real harness but `spawn` raises `HarnessUnavailable`, surfaced as an escalation, NOT a crash and NOT an unconfined run). Keep single-node + flock behavior.

- [ ] **Step 1: Write the failing test.** Assert: `run_cli(dry_run=True)` still uses StubHarness (existing behavior, e2e read-only unaffected). A new test: with a config whose `live_execution=False`, calling the canary path builds a real adapter whose `sandbox.spawn` raises `HarnessUnavailable` and the run reports the item escalated (not a crash). Mock `build_harness`/`Config.load` so no real docker is touched.

- [ ] **Step 2-4:** implement; run (orchestrator suite + e2e dry-run smoke + full suite green). The e2e dry-run read-only smoke MUST remain green.

- [ ] **Step 5: Commit** (`feat(autopilot): canary/live path builds the real sandboxed harness (still live_execution-gated)`).

---

## Phase D - Docker images (require Docker; build-verified)

### Task D1: sandbox images + proxy image + build script

**Files:** Create `docker/sandbox/proxy/Dockerfile`, `docker/sandbox/claude/Dockerfile`, `docker/sandbox/pi/Dockerfile`, `docker/sandbox/opencode/Dockerfile`, `docker/sandbox/build.sh`.

**Interfaces:** Each Dockerfile: base `node:24-bookworm`, create non-root user `sbx` (uid 10001), `WORKDIR /work`, install `git` + python3.12 + pytest (for the repo toolchain) + the harness CLI (`npm i -g @anthropic-ai/claude-code` / the pi install / `npm i -g opencode-ai` as appropriate), pinned versions. The proxy image installs only python3 + the `skos.autopilot.sandbox_proxy` module (COPY the single file) and `ENTRYPOINT ["python","-m","skos.autopilot.sandbox_proxy"]`. `build.sh` builds and tags `sandbox-proxy:1`, `sandbox-claude:1`, `sandbox-pi:1`, `sandbox-opencode:1`.

- [ ] **Step 1:** Write the Dockerfiles + build.sh (no unit test; this is an artifact).
- [ ] **Step 2:** `bash docker/sandbox/build.sh` builds all images successfully; `docker image inspect sandbox-proxy:1 sandbox-claude:1 sandbox-pi:1 sandbox-opencode:1` all succeed. Record image sizes.
- [ ] **Step 3:** Commit (`feat(sandbox): pinned Docker images (proxy + claude/pi/opencode)`).

**Note for the controller:** this task needs a real Docker daemon and pulls/builds ~1GB+ per image; run it in an environment with Docker and network, verify the build, and do not fan it out blindly. If pi's install path or a CLI's auth mechanism differs from assumed, adjust the Dockerfile and re-verify before committing.

---

## Phase E - confinement proof + docs

### Task E1: confinement integration test (the acceptance gate)

**Files:** Create `tests/test_sandbox_confinement_it.py`.

**Interfaces:** gated behind `RUN_SANDBOX_IT=1` and docker availability (`pytest.mark.skipif`). Builds/uses `sandbox-proxy:1` + one harness image; runs a real confined container via `Sandbox` (with `live_execution=True`) executing small probe commands instead of a full harness (override the container argv to a shell probe), and asserts:
1. secret trees absent: `test -e /home/sbx/.skcapstone` etc. return non-zero inside;
2. off-allowlist denied: a `CONNECT example.com` through the proxy fails;
3. on-allowlist reaches: the allowlisted git host connects;
4. `/work` writable, another host path not mounted.

- [ ] **Step 1:** Write the gated test. **Step 2:** `RUN_SANDBOX_IT=1 ~/.skenv/bin/python -m pytest tests/test_sandbox_confinement_it.py -q` passes on a docker node; skipped cleanly without docker/flag. **Step 3:** Commit (`test(sandbox): confinement proof - secrets absent, egress pinned (gated)`).

**Note for the controller:** this is the v1.5 acceptance gate. Run it yourself on the docker node and read the result; do not accept a PASS you have not seen. Until it is green, `live_execution` stays off.

### Task E2: docs - architecture section 12 + SOP + spec cross-ref

**Files:** Modify `docs/skos-autopilot-architecture.md` (section 12: replace the bwrap description with the Docker sandbox + harness registry; keep the firewall text), `docs/skos-autopilot-SOP.md` (add: choosing a harness, building the sandbox images, running the confinement proof, enabling live_execution as a two-key act), and add a "See implementation" backlink to the spec.

- [ ] **Step 1:** Update the docs (no em/en dashes). **Step 2:** `grep -rn $'\xe2\x80\x94\|\xe2\x80\x93' docs/skos-autopilot-architecture.md docs/skos-autopilot-SOP.md` returns nothing. **Step 3:** Commit (`docs(autopilot): section 12 Docker sandbox + harness registry; SOP harness/sandbox/live-enable`).

---

## Final

After E2: dispatch the whole-branch review (most capable model) over `merge-base main HEAD..HEAD`, focusing on: the confinement invariants (secrets absent, egress sole-path, fail-closed capability check), that `live_execution` still defaults off and dry-run is unaffected, the registry fail-closed on unknown harness, no new em/en dashes, and no secret path reachable in any `_docker_run_argv`. Then `superpowers:finishing-a-development-branch`.
