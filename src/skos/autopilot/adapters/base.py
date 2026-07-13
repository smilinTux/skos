"""BaseCliAdapter: the shared harness seam. Holds a Sandbox and composes the
assess/run_task/grade prompts (framing untrusted text as data), building a
LaunchSpec the sandbox runs. Concrete adapters supply only what varies."""
from __future__ import annotations

import json
import os
import subprocess

from ..claude_code import frame
from ..sandbox import LaunchSpec
from ..types import (AssessBrief, GateResult, GradeBrief, HarnessResult,
                     TaskBrief, Verdict)


def parse_event_stream(body: str) -> dict:
    """Extract the model's final JSON reply from a newline-delimited JSON event
    stream (opencode `--format json`, pi `--mode json`). The reply is the last
    `text` event's `part.text` (or a top-level `text`), json-decoded. Returns {}
    when no decodable reply is present. Grounded in a captured opencode sample:
    events like {"type":"text","part":{"type":"text","text":"{...json...}"}}.
    """
    text = None
    for line in (body or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(ev, dict):
            continue
        part = ev.get("part")
        if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
            text = part["text"]
        elif ev.get("type") == "text" and isinstance(ev.get("text"), str):
            text = ev["text"]
    if text:
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


class BaseCliAdapter:
    name = "base"

    def __init__(self, sandbox, egress_hosts=None, live_execution: bool = False):
        self.sandbox = sandbox
        self.egress_hosts = list(egress_hosts or [])
        self.live_execution = live_execution

    # -- hooks each concrete adapter provides --
    def _argv(self, prompt: str) -> list[str]: raise NotImplementedError
    def _image(self) -> str: raise NotImplementedError
    def _auth_mounts(self) -> list: raise NotImplementedError
    def _auth_env(self) -> dict: raise NotImplementedError
    def _parse(self, raw: dict) -> dict: raise NotImplementedError
    def capabilities(self): raise NotImplementedError

    # -- egress host derivation --
    def _remote_host(self, repo):
        if repo is None:
            return None
        try:
            r = subprocess.run(["git", "-C", repo.path, "remote", "get-url", "origin"],
                               capture_output=True, text=True)
        except OSError:
            return None
        url = (r.stdout or "").strip()
        if not url:
            return None
        if url.startswith("git@"):
            return url.split("@", 1)[1].split(":", 1)[0]
        if "://" in url:
            return url.split("://", 1)[1].split("@")[-1].split("/", 1)[0].split(":", 1)[0]
        return None

    def _ci_host(self, repo):
        if repo is None:
            return None
        if str(getattr(repo, "ci", "none")).startswith("github"):
            return "api.github.com"
        return None

    # -- shared spawn helpers --
    def _run_raw(self, instruction: str, data: str, *, worktree: str, repo) -> dict:
        prompt = frame(instruction, data)
        image = getattr(repo, "sandbox_image", None) or self._image()
        spec = LaunchSpec(name=self.name, argv=self._argv(prompt), image=image,
                          worktree=worktree, auth_mounts=self._auth_mounts(),
                          auth_env=self._auth_env(), egress_hosts=self.egress_hosts)
        return self.sandbox.spawn(spec, repo_remote_host=self._remote_host(repo),
                                  ci_host=self._ci_host(repo))

    def _run(self, instruction: str, data: str, *, worktree: str, repo) -> dict:
        return self._parse(self._run_raw(instruction, data, worktree=worktree, repo=repo))

    # -- the three seam methods (prompts copied verbatim from ClaudeCodeAdapter) --
    def assess(self, brief: AssessBrief) -> Verdict:
        instruction = (
            "Assess whether a coord task is still valid work. Reply strictly as "
            "JSON: {\"verdict\":\"valid|stale|obsolete|needs_decision\","
            "\"reason\":\"...\"}.")
        data = json.dumps({"task_id": brief.task_id, "title": brief.title,
                           "description": brief.description,
                           "acceptance": brief.acceptance, "tags": brief.tags,
                           "codebase_context": brief.codebase_context})
        out = self._run(instruction, data, worktree=os.getcwd(), repo=None)
        return Verdict(verdict=out.get("verdict", "needs_decision"),
                       reason=out.get("reason", ""),
                       updated_description=out.get("updated_description"),
                       updated_acceptance=out.get("updated_acceptance"))

    def run_task(self, brief: TaskBrief) -> HarnessResult:
        instruction = (
            "Implement the task in the current git worktree, test-driven "
            "(failing test first). Match the repo's conventions.")
        data = json.dumps({"task_id": brief.task_id, "title": brief.title,
                           "description": brief.description,
                           "acceptance": brief.acceptance,
                           "prior_feedback": brief.prior_feedback, "round": brief.round})
        raw = self._run_raw(instruction, data, worktree=brief.worktree, repo=brief.repo)
        usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
        return HarnessResult(
            ok=(not bool(raw.get("is_error"))) and int(raw.get("exit_code", 0) or 0) == 0,
            artifact=brief.worktree,
            tokens=int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0)),
            cost_usd=float(raw.get("total_cost_usd", 0.0) or 0.0),
            raw=raw)

    def grade(self, brief: GradeBrief) -> GateResult:
        instruction = (
            "You are an independent grader. Score the diff 1-5 against the "
            "acceptance criteria and CI status. Reply strictly as JSON: "
            "{\"score\":N,\"passed\":bool,\"notes\":\"...\"}.")
        data = json.dumps({"task_id": brief.task_id, "diff": brief.diff,
                           "acceptance": brief.acceptance, "ci_status": brief.ci_status,
                           "diff_coverage": brief.diff_coverage})
        out = self._run(instruction, data, worktree=brief.worktree, repo=brief.repo)
        return GateResult(score=out.get("score"), passed=bool(out.get("passed")),
                          notes=out.get("notes", ""), artifact=out.get("artifact"))
