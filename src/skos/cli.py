"""skos operator CLI. Mirrors `opensrc path` ergonomics — `skos path <subdir>` prints abs path."""
from __future__ import annotations

import typer

from skos import paths, profile, registry
from skos.descriptor import load_descriptor
from skos.packaging.oci import OciAdapter

app = typer.Typer(help="skos — filesystem & packaging foundation")


@app.command()
def path(subdir: str):
    """Print the absolute path of a data-root subdir."""
    typer.echo(str(paths.subdir(subdir)))


@app.command(name="profile")
def show_profile():
    """Print the active topology profile and its data root."""
    typer.echo(f"{profile.active().value}\t{paths.data_root()}")


@app.command()
def describe(app_yaml: str):
    """Validate and summarize an app.yaml descriptor."""
    d = load_descriptor(app_yaml)
    typer.echo(f"{d.name}\t{d.capability}\t{list(d.packaging.model_dump(exclude_none=True))}")


@app.command(name="list")
def list_apps():
    """List installed apps from the registry."""
    for name, meta in registry.list_installed().items():
        typer.echo(f"{name}\t{meta['adapter']}\t{meta['ref']}")


@app.command()
def install(app_yaml: str):
    """Materialize an app via its packaging adapter and record it."""
    d = load_descriptor(app_yaml)
    adapter = OciAdapter()  # resolver picks adapter per profile in a later sub-project
    res = adapter.materialize(d)
    registry.record(d.name, adapter=res.adapter, ref=res.ref)
    typer.echo(f"installed {d.name} via {res.adapter} ({res.ref}) running={res.running}")
