"""Confinement proof (acceptance gate) for the autopilot Docker sandbox.

Gated behind RUN_SANDBOX_IT=1 and docker availability, and requires the pinned
images (sandbox-proxy:1, sandbox-claude:1) built via docker/sandbox/build.sh. It
runs a REAL confined container through Sandbox and proves, not asserts in prose:
  1. host secret trees are absent inside the container;
  2. an off-allowlist host is denied by the egress proxy (403);
  3. an on-allowlist host is not denied (the allowlist distinguishes them);
  4. the worktree is writable and the container rootfs is read-only;
  5. a RAW connection with no proxy involved also fails, proving the
     `--internal` network itself has no external route (not just that the
     proxy is the one saying no).

Until this passes on a node, harness.live_execution must stay off there.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from skos.autopilot.sandbox import LaunchSpec, Sandbox

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_SANDBOX_IT") or not shutil.which("docker"),
    reason="integration: set RUN_SANDBOX_IT=1 and have docker + the sandbox images")


def _image_present(tag: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", tag],
                          capture_output=True).returncode == 0


PROBE = r'''
import json, os, socket
r = {}
r["skcapstone_absent"] = not os.path.exists(os.path.expanduser("~/.skcapstone"))
r["ssh_absent"] = not os.path.exists(os.path.expanduser("~/.ssh"))
r["hermes_absent"] = not os.path.exists(os.path.expanduser("~/.hermes"))
try:
    open("/work/.sbxprobe", "w").write("x"); r["work_writable"] = True
except OSError:
    r["work_writable"] = False
try:
    open("/etc/sbxprobe", "w").write("x"); r["rootfs_writable"] = True
except OSError:
    r["rootfs_writable"] = False
def status(host):
    px = os.environ.get("HTTPS_PROXY", "").split("//", 1)[-1]
    ph, pp = px.split(":")
    s = socket.create_connection((ph, int(pp)), timeout=10)
    s.sendall(("CONNECT %s:443 HTTP/1.1\r\nHost: %s:443\r\n\r\n" % (host, host)).encode())
    line = s.recv(256).decode("latin1").splitlines()[0]
    s.close()
    return line
try:
    r["deny"] = status("example.com")
except Exception as e:
    r["deny"] = "ERR:%s" % e
try:
    r["allow"] = status("github.com")
except Exception as e:
    r["allow"] = "ERR:%s" % e
try:
    socket.create_connection(("example.com", 443), timeout=6).close()
    r["direct_egress"] = "REACHED"          # would be a confinement hole
except OSError:
    r["direct_egress"] = "blocked"
print(json.dumps(r))
'''


def test_sandbox_confinement(tmp_path):
    for tag in ("sandbox-proxy:1", "sandbox-claude:1"):
        if not _image_present(tag):
            pytest.skip(f"image {tag} not built; run docker/sandbox/build.sh")

    spec = LaunchSpec(name="probe", argv=["python3", "-c", PROBE],
                      image="sandbox-claude:1", worktree=str(tmp_path),
                      auth_mounts=[], auth_env={}, egress_hosts=[])
    out = Sandbox(live_execution=True).spawn(spec, repo_remote_host="github.com",
                                             ci_host=None)

    # spawn parses the probe's JSON stdout
    assert isinstance(out, dict), out
    # 1. secrets confined by absence
    assert out.get("skcapstone_absent") is True, out
    assert out.get("ssh_absent") is True, out
    assert out.get("hermes_absent") is True, out
    # 4. worktree writable, rootfs read-only
    assert out.get("work_writable") is True, out
    assert out.get("rootfs_writable") is False, out
    # 2. off-allowlist denied by the proxy
    assert "403" in str(out.get("deny", "")), out
    # 3. on-allowlist not denied (200 tunneled, or 502 if upstream unreachable)
    assert "403" not in str(out.get("allow", "")), out
    # 5. no direct route out of the --internal network, proxy bypass excluded
    assert out.get("direct_egress") == "blocked", out
