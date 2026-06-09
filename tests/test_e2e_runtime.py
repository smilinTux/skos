import shutil, pytest
from skos.packaging import runtime

pytestmark = pytest.mark.skipif(
    not (shutil.which("podman") or shutil.which("docker")),
    reason="no container runtime available",
)


def test_runtime_runs_hello():
    r = runtime.run("run", "--rm", "docker.io/library/hello-world", check=False)
    assert r.returncode == 0 or "Hello" in (r.stdout + r.stderr)
