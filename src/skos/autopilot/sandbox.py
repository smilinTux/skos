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
        raise NotImplementedError("Sandbox.spawn network/proxy lifecycle lands in A3")
