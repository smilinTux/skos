"""OCI packaging adapter — pulls/runs an OCI image via the runtime seam. Image = the contract."""
from __future__ import annotations

from skos.descriptor import AppDescriptor
from skos.packaging import runtime
from skos.packaging.base import InstallResult, PackagingAdapter


class OciAdapter(PackagingAdapter):
    name = "oci"

    def _container(self, app: AppDescriptor) -> str:
        return f"skos-{app.name}"

    def materialize(self, app: AppDescriptor) -> InstallResult:
        image = app.packaging.oci.image
        runtime.run("pull", image)
        ports = []
        for p in app.packaging.oci.ports:
            ports += ["-p", f"{p}:{p}"]
        runtime.run("run", "-d", "--name", self._container(app), *ports, image)
        return InstallResult(name=app.name, adapter=self.name, ref=image,
                             running=self.health(app))

    def locate(self, app: AppDescriptor) -> str | None:
        out = runtime.run("images", "-q", app.packaging.oci.image, check=False).stdout.strip()
        return app.packaging.oci.image if out else None

    def health(self, app: AppDescriptor) -> bool:
        out = runtime.run("ps", "-q", "-f", f"name={self._container(app)}", check=False).stdout.strip()
        return bool(out)

    def remove(self, app: AppDescriptor) -> None:
        runtime.run("rm", "-f", self._container(app), check=False)
