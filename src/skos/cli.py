"""skos operator CLI. Mirrors `opensrc path` ergonomics — `skos path <subdir>` prints abs path."""
from __future__ import annotations

import typer

from skos import paths, profile as _profile_module, registry
from skos.capability import Catalog
from skos.descriptor import load_descriptor
from skos.packaging.oci import OciAdapter
from skos import resolver as _resolver

app = typer.Typer(help="skos — filesystem & packaging foundation")


@app.command()
def path(subdir: str):
    """Print the absolute path of a data-root subdir."""
    typer.echo(str(paths.subdir(subdir)))


@app.command(name="profile")
def show_profile():
    """Print the active topology profile and its data root."""
    typer.echo(f"{_profile_module.active().value}\t{paths.data_root()}")


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


@app.command()
def capabilities():
    """List the capability catalog grouped by the 4 C's."""
    cat = Catalog.load()
    for group in ("cloud", "comms", "compute", "core"):
        typer.echo(f"\n[{group}]")
        for c in cat.by_group(group):
            alts = f"  (alt: {', '.join(c.alternates)})" if c.alternates else ""
            typer.echo(f"  {c.name:9} {c.default:14} {c.description}{alts}")


@app.command()
def resolve(capability: str, profile: str = "", adapter: str = ""):
    """Resolve which adapter a capability uses for a profile (override with --adapter)."""
    prof = profile or _profile_module.active().value
    try:
        chosen = _resolver.resolve(capability, profile=prof, override=adapter or None)
    except _resolver.ResolveError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(f"{capability}\t{prof}\t{chosen}")


secret_app = typer.Typer(help="skvault — sovereign secret storage")
app.add_typer(secret_app, name="secret")


def _split(ref: str):
    scope, _, key = ref.partition("/")
    if not key:
        raise typer.BadParameter("use scope/key, e.g. cloud/cf_token")
    return scope, key


@secret_app.command("set")
def secret_set(ref: str, value: str):
    from skos import secrets
    s, k = _split(ref); secrets.get_backend().set(s, k, value)
    typer.echo(f"stored {ref}")


@secret_app.command("get")
def secret_get(ref: str):
    from skos import secrets
    s, k = _split(ref); typer.echo(secrets.get_backend().get(s, k))


@secret_app.command("list")
def secret_list(scope: str = ""):
    from skos import secrets
    for k in secrets.get_backend().list(scope or None):
        typer.echo(k)


@secret_app.command("rm")
def secret_rm(ref: str):
    from skos import secrets
    s, k = _split(ref); secrets.get_backend().delete(s, k); typer.echo(f"deleted {ref}")
