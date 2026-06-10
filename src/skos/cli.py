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


@app.command()
def render(
    app_yaml: str,
    platform: str = typer.Option(..., "--platform", help="Target platform: compose, swarm, kubernetes"),
):
    """Render an app.yaml descriptor to a platform deployment manifest."""
    from skos.render import RENDERERS, get_renderer
    supported = sorted(RENDERERS)
    if platform not in RENDERERS:
        typer.echo(
            f"error: unknown platform {platform!r}. Supported: {', '.join(supported)}",
            err=True,
        )
        raise typer.Exit(1)
    d = load_descriptor(app_yaml)
    renderer = get_renderer(platform)
    typer.echo(renderer.render(d))


# ---------------------------------------------------------------------------
# Topology installer commands: init / plan / up
# ---------------------------------------------------------------------------

def _parse_install_profile(profile_str: str):
    """Parse a profile string to InstallProfile, echoing an error on failure."""
    from skos.install.profiles import InstallProfile
    try:
        return InstallProfile(profile_str.lower())
    except ValueError:
        valid = [p.value for p in InstallProfile]
        typer.echo(f"error: unknown profile {profile_str!r}; choose from {valid}", err=True)
        raise typer.Exit(2)


@app.command(name="init")
def init_cmd(
    profile: str = typer.Option("local", "--profile", "-p",
                                help="Topology profile: local | cluster | cloud"),
):
    """Set up the data-root tree and show the PERSONAL-FIRST recommended capability set."""
    from skos.install.profiles import recommended
    from skos.install.provisioner import apply as _apply
    from skos.install.planner import plan as _plan

    prof = _parse_install_profile(profile)
    paths.ensure_tree()
    caps = recommended(prof)
    typer.echo(f"profile : {prof.value}")
    typer.echo(f"data-root: {paths.data_root()}")
    typer.echo(f"\nRecommended capabilities ({len(caps)}):")
    for cap in caps:
        typer.echo(f"  {cap}")
    typer.echo("\nRun `skos plan` to see resolved adapters, `skos up` to apply.")


@app.command(name="plan")
def plan_cmd(
    profile: str = typer.Option("local", "--profile", "-p",
                                help="Topology profile: local | cluster | cloud"),
    cap: list[str] = typer.Option([], "--cap", "-c",
                                  help="Explicit capability name (repeatable); omit to use profile defaults"),
):
    """Show the resolved install plan (capability → adapter) without applying it."""
    from skos.install.planner import plan as _plan, PlanError

    prof = _parse_install_profile(profile)
    caps = list(cap) if cap else None
    try:
        install_plan = _plan(prof, capabilities=caps)
    except PlanError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Install plan  profile={install_plan.profile}  steps={len(install_plan.steps)}")
    typer.echo(f"{'capability':<14}  {'adapter'}")
    typer.echo("-" * 36)
    for step in install_plan.steps:
        typer.echo(f"{step.capability:<14}  {step.adapter}")


@app.command(name="up")
def up_cmd(
    profile: str = typer.Option("local", "--profile", "-p",
                                help="Topology profile: local | cluster | cloud"),
    cap: list[str] = typer.Option([], "--cap", "-c",
                                  help="Explicit capability name (repeatable); omit to use profile defaults"),
):
    """Apply the install plan: ensure data-root tree + record capabilities in the registry."""
    from skos.install.planner import plan as _plan, PlanError
    from skos.install.provisioner import apply as _apply

    prof = _parse_install_profile(profile)
    caps = list(cap) if cap else None
    try:
        install_plan = _plan(prof, capabilities=caps)
    except PlanError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)

    result = _apply(install_plan)
    typer.echo(f"Applied plan  profile={install_plan.profile}  "
               f"recorded={result.recorded_count}  planned={result.planned_count}")
    for outcome in result.outcomes:
        marker = "✓" if outcome.status == "recorded" else "~"
        note = f"  [{outcome.note}]" if outcome.note else ""
        typer.echo(f"  {marker} {outcome.capability:<14}  {outcome.adapter}  ({outcome.status}){note}")


secret_app = typer.Typer(help="skvault — sovereign secret storage")
app.add_typer(secret_app, name="secret")


# ---------------------------------------------------------------------------
# Brain sub-commands: init / index / validate
# ---------------------------------------------------------------------------

brain_app = typer.Typer(help="skos brain — Infinite Brain entity-graph ontology")
app.add_typer(brain_app, name="brain")


@brain_app.command("init")
def brain_init_cmd(
    wiki: str = typer.Option("", "--wiki", help="Override wiki root path"),
):
    """Scaffold the entity-graph skeleton + self-build prompt under the wiki."""
    from skos.brain.brain_init import scaffold, CORE_NAMESPACES
    from pathlib import Path

    wiki_root = Path(wiki).expanduser().resolve() if wiki else None
    try:
        result = scaffold(wiki_root=wiki_root)
    except Exception as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)

    typer.echo(f"skos brain init — entity-graph skeleton scaffolded")
    typer.echo(f"  wiki root  : {(wiki_root or Path('~/clawd/wiki').expanduser().resolve())}")
    typer.echo(f"  namespaces : {len(result)}")
    for ns, idx_path in result.items():
        status = "created" if idx_path.exists() else "exists"
        typer.echo(f"    {ns:<16} {idx_path}")
    typer.echo("")
    typer.echo("Next: run the self-build prompt to flesh out the entity graph.")
    typer.echo("  Prompt: <wiki>/pages/entities/build_prompt.md")
    typer.echo("  Open it in Claude Code and follow the instructions.")


@brain_app.command("index")
def brain_index_cmd(
    namespace: str = typer.Argument(..., help="Namespace directory name or full path"),
    wiki: str = typer.Option("", "--wiki", help="Override wiki root path"),
):
    """Build (or rebuild) _index.md for a namespace."""
    from skos.brain.index import build_index
    from pathlib import Path
    import os

    # Resolve: if namespace looks like a path, use it directly; else resolve under wiki
    p = Path(namespace)
    if not p.is_absolute():
        wiki_root = (Path(wiki).expanduser().resolve() if wiki
                     else Path(os.environ.get("SKOS_WIKI_ROOT", "~/clawd/wiki")).expanduser().resolve())
        p = wiki_root / "pages" / "entities" / namespace

    try:
        index_path = build_index(p)
    except FileNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)
    except Exception as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)

    from skos.brain.index import read_index
    entries = read_index(p)
    typer.echo(f"Built index: {index_path}")
    typer.echo(f"  {len(entries)} entities indexed")


@brain_app.command("validate")
def brain_validate_cmd(
    file: str = typer.Argument(..., help="Path to an entity node .md file"),
):
    """Validate an entity node file against the EntityNode schema."""
    from skos.brain.entity import parse, ParseError
    from pathlib import Path

    p = Path(file).expanduser().resolve()
    if not p.exists():
        typer.echo(f"error: file not found: {p}", err=True)
        raise typer.Exit(1)

    try:
        node = parse(p.read_text(encoding="utf-8"))
    except ParseError as exc:
        typer.echo(f"INVALID  {p.name}", err=True)
        typer.echo(f"  {exc}", err=True)
        raise typer.Exit(1)

    typer.echo(f"OK  {node.id}  [{node.type}]  {node.namespace}  ({node.lifecycle_state})")
    typer.echo(f"    summary: {node.summary[:80]}")
    if node.edges:
        typer.echo(f"    edges  : {len(node.edges)}")


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
