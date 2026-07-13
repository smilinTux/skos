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
            # run as the host uid:gid so the bind-mounted worktree is writable;
            # still non-root-privileged (caps dropped, no-new-privileges, read-only
            # rootfs, no docker socket), so confinement holds.
            "--user", f"{os.getuid()}:{os.getgid()}", "--workdir", "/work",
            "--read-only", "--tmpfs", "/tmp:mode=1777",
            "--tmpfs", "/home/sbx:mode=1777",       # writable HOME for an arbitrary uid
            "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
            "--pids-limit", "512",
            "--env", "HOME=/home/sbx",
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
        net = f"sbxnet-{os.getpid()}-{len(allow)}"
        proxy_alias = "sbxproxy"
        proxy_name = f"{proxy_alias}-{os.getpid()}"
        try:
            subprocess.run([self.docker, "network", "create", "--internal", net],
                           capture_output=True, text=True, check=True)
            # proxy sidecar: dual-homed (internal net + default bridge) so it is the
            # ONLY route out; started with the pinned allowlist; reached by alias.
            subprocess.run(
                [self.docker, "run", "-d", "--name", proxy_name, "--network", net,
                 "--network-alias", proxy_alias, "sandbox-proxy:1",
                 "python", "-m", "skos.autopilot.sandbox_proxy", str(PROXY_PORT), *allow],
                capture_output=True, text=True, check=True)
            subprocess.run([self.docker, "network", "connect", "bridge", proxy_name],
                           capture_output=True, text=True)          # give proxy outward egress
            proc = subprocess.run(self._docker_run_argv(spec, net, proxy_alias),
                                  capture_output=True, text=True, cwd=spec.worktree)
            try:
                return json.loads(proc.stdout or "{}")
            except json.JSONDecodeError:
                return {"result": proc.stdout, "stderr": proc.stderr, "exit_code": proc.returncode}
        finally:
            subprocess.run([self.docker, "rm", "-f", proxy_name], capture_output=True, text=True)
            subprocess.run([self.docker, "network", "rm", net], capture_output=True, text=True)
