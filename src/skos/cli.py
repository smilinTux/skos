"""skos operator CLI. Mirrors `opensrc path` ergonomics: `skos path <subdir>` prints abs path."""
from __future__ import annotations

import typer

from skos import paths, profile as _profile_module, registry
from skos import skworld_manifest as _skworld_manifest
from skos.capability import Catalog
from skos.descriptor import load_descriptor
from skos.packaging.oci import OciAdapter
from skos import resolver as _resolver

app = typer.Typer(help="skos: filesystem & packaging foundation")


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
def status(
    section: str = typer.Argument("all", help="email|cron|gtd|docs|corpus|calendar|all|report|corpus-check"),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Realtime skos status across email, cron/scheduled work, docs, corpus, and GTD."""
    from skos import status as _status
    _status.run([section] + (["--json"] if json_out else []))


@app.command()
def ingest(
    adapter: str = typer.Argument(..., help="gtd-ingest pull adapter to drain: calendar | telegram | email | order"),
    add: bool = typer.Option(False, "--add", help="seed a tracked order (adapter=order) instead of draining"),
    order_id: str = typer.Option(None, "--order-id", help="vendor order id (dedup key)"),
    account: str = typer.Option(None, "--account", help="gog mailbox that receives the vendor's status emails"),
    vendor: str = typer.Option("amazon", "--vendor", help="vendor tag (amazon, ...)"),
    eta: str = typer.Option(None, "--eta", help="expected delivery date, e.g. 2026-07-05"),
    text: str = typer.Option(None, "--text", help="human label for the GTD item"),
):
    """Drain a gtd-ingest PULL adapter once (poll -> capture into the unified GTD).

    With ``--add`` (adapter=order): seed a tracked order/delivery instead of
    draining, e.g.::

        skos ingest order --add --order-id 113-… --account you@gmail.com \\
            --eta 2026-07-05 --text "iPhone 13 mini battery ×2"
    """
    if add:
        if adapter != "order":
            raise typer.BadParameter("--add is only supported for the 'order' adapter")
        if not order_id or not account:
            raise typer.BadParameter("--order-id and --account are required with --add")
        from skos.adapters.order import seed_order
        iid, action = seed_order(order_id, account, vendor=vendor, eta=eta, text=text)
        typer.echo(f"order {order_id}: {action} ({iid})")
        return
    from skos import adapters as _ad
    typer.echo(f"{adapter}: captured {_ad.drain(adapter)} new GTD item(s)")


gtd_app = typer.Typer(help="Unified GTD store: capture/upsert through the ONE locked sink")
app.add_typer(gtd_app, name="gtd")


def _gtd_capture_obj(text, source, source_ref, context, priority, privacy,
                     status, delegate_to, meta):
    import json as _json
    from skos.gtd_ingest import GtdCapture
    try:
        m = _json.loads(meta) if meta else {}
        if not isinstance(m, dict):
            raise ValueError("--meta must be a JSON object")
    except ValueError as e:
        raise typer.BadParameter(f"--meta: {e}") from e
    return GtdCapture(text=text, source=source, source_ref=source_ref,
                      context=context, priority=priority or None, privacy=privacy,
                      status=status, delegate_to=delegate_to or None, meta=m)


@gtd_app.command("capture")
def gtd_capture(
    text: str = typer.Argument(..., help="Item text"),
    source: str = typer.Option("manual", "--source", help="itil|email|cron|telegram|voice|calendar|manual"),
    source_ref: str = typer.Option("", "--source-ref", help="Stable dedup key; dedupes across the WHOLE store"),
    context: str = typer.Option("@inbox", "--context"),
    priority: str = typer.Option("", "--priority", help="critical|high|medium|low"),
    privacy: str = typer.Option("private", "--privacy"),
    status: str = typer.Option("inbox", "--status", help="inbox|next|project|waiting|someday|reference"),
    delegate_to: str = typer.Option("", "--delegate-to"),
    meta: str = typer.Option("", "--meta", help="JSON object of source-specific fields"),
):
    """Create-or-skip capture into the unified GTD (dedupe by source+source_ref)."""
    from skos.gtd_ingest import capture as _capture
    iid = _capture(_gtd_capture_obj(text, source, source_ref, context, priority,
                                    privacy, status, delegate_to, meta))
    if iid is None:
        typer.echo(f"duplicate: ({source}, {source_ref}) already in store")
    else:
        typer.echo(f"captured {iid}")


@gtd_app.command("upsert")
def gtd_upsert(
    text: str = typer.Argument(..., help="Item text"),
    source: str = typer.Option("manual", "--source"),
    source_ref: str = typer.Option("", "--source-ref", help="Stable key; create-or-update"),
    context: str = typer.Option("@inbox", "--context"),
    priority: str = typer.Option("", "--priority"),
    privacy: str = typer.Option("private", "--privacy"),
    status: str = typer.Option("inbox", "--status", help="inbox|next|project|waiting|someday|reference|done"),
    delegate_to: str = typer.Option("", "--delegate-to"),
    meta: str = typer.Option("", "--meta", help="JSON object of source-specific fields"),
):
    """Create-or-update a stateful item (moves lists on status change; done archives)."""
    from skos.gtd_ingest import upsert as _upsert
    iid, action = _upsert(_gtd_capture_obj(text, source, source_ref, context, priority,
                                           privacy, status, delegate_to, meta))
    typer.echo(f"{action} {iid}")


@gtd_app.command("replay-errors")
def gtd_replay_errors():
    """Replay the sink's error-recovery queue: move each quarantined
    ``*.corrupt-*`` store file into the ``.replay`` staging subdir for
    reprocessing/forensics. Reversible (files are relocated, never deleted) and
    low blast. This is what `skos operator act replay_errors` actuates."""
    from skos.gtd_ingest import replay_quarantine
    moved = replay_quarantine()
    if not moved:
        typer.echo("clean: no quarantined items to replay")
        return
    for name in moved:
        typer.echo(f"  replayed: {name}")
    typer.echo(f"({len(moved)} quarantined file(s) staged for reprocessing)")


placement_app = typer.Typer(help="Storage-placement policy engine + blob catalog")
app.add_typer(placement_app, name="placement")


def _parse_attrs(attrs_json: str) -> dict:
    import json as _json
    try:
        a = _json.loads(attrs_json) if attrs_json else {}
        if not isinstance(a, dict):
            raise ValueError("attrs must be a JSON object")
        return a
    except ValueError as e:
        raise typer.BadParameter(f"--attrs: {e}") from e


@placement_app.command("resolve")
def placement_resolve(
    attrs: str = typer.Argument(..., help="JSON object of blob attrs (mimetype,size,tags,source,ext)"),
    policy: str = typer.Option("", "--policy", help="placement.yaml path (default: config/placement.yaml)"),
):
    """Resolve (pure, no record) which store/tier/node a blob would land on."""
    from skos import placement as _pl
    pol = _pl.load_policy(policy or None)
    p = _pl.resolve_placement(_parse_attrs(attrs), pol)
    typer.echo(f"{p.store}\t{p.node}\t{p.tier}\t{p.annex or '-'}\trule={p.rule}")


@placement_app.command("wanted")
def placement_wanted(
    store: str = typer.Argument(..., help="Store name to render a git-annex preferred-content expr for"),
    policy: str = typer.Option("", "--policy", help="placement.yaml path"),
):
    """Render the git-annex `wanted` (preferred-content) expression for a store.

    Wiring it via `git annex wanted <remote> <expr>` is deferred; this prints it."""
    from skos import placement as _pl
    pol = _pl.load_policy(policy or None)
    typer.echo(_pl.preferred_content_expr(pol, store))


@placement_app.command("show")
def placement_show(
    blob_id: str = typer.Argument(..., help="Blob id to look up in the catalog"),
):
    """Print the catalog row for a blob id (exit 1 if absent)."""
    import json as _json
    from skos import placement as _pl
    row = _pl.get_placement(blob_id)
    if row is None:
        typer.echo(f"no catalog entry for {blob_id!r}")
        raise typer.Exit(1)
    typer.echo(_json.dumps(row, indent=2, ensure_ascii=False))


@placement_app.command("list")
def placement_list():
    """List all blob-catalog rows (blob_id, store, node, tier)."""
    from skos import placement as _pl
    for row in _pl.list_placements():
        typer.echo(f"{row.get('blob_id')}\t{row.get('store')}\t{row.get('node')}\t{row.get('tier')}")


@app.command()
def store(
    blob_id: str = typer.Argument(..., help="Blob id (content hash or stable key)"),
    attrs: str = typer.Option("", "--attrs", help="JSON object of blob attrs (mimetype,size,tags,source,ext)"),
    policy: str = typer.Option("", "--policy", help="placement.yaml path"),
):
    """`skos store`: resolve a blob's placement from policy and record it in the
    blob catalog (record_ingest_location). Upsert-by-blob-id; prints the target."""
    from skos import placement as _pl
    pol = _pl.load_policy(policy or None)
    row = _pl.store_blob(blob_id, _parse_attrs(attrs), pol)
    typer.echo(f"stored {blob_id} -> {row['store']} ({row['node']}/{row['tier']}) rule={row['rule']}")


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


secret_app = typer.Typer(help="skvault: sovereign secret storage")
app.add_typer(secret_app, name="secret")


# ---------------------------------------------------------------------------
# Brain sub-commands: init / index / validate
# ---------------------------------------------------------------------------

brain_app = typer.Typer(help="skos brain: Infinite Brain entity-graph ontology")
app.add_typer(brain_app, name="brain")


@brain_app.command("init")
def brain_init_cmd(
    wiki: str = typer.Option("", "--wiki", help="Override wiki root path"),
):
    """Scaffold the entity-graph skeleton + self-build prompt under the wiki."""
    from skos.brain.brain_init import scaffold
    from pathlib import Path

    wiki_root = Path(wiki).expanduser().resolve() if wiki else None
    try:
        result = scaffold(wiki_root=wiki_root)
    except Exception as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)

    typer.echo("skos brain init: entity-graph skeleton scaffolded")
    typer.echo(f"  wiki root  : {(wiki_root or Path('~/clawd/wiki').expanduser().resolve())}")
    typer.echo(f"  namespaces : {len(result)}")
    for ns, idx_path in result.items():
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


# ---------------------------------------------------------------------------
# Surface sub-commands: list / ls / read / write  (runtime-adapter layer)
# ---------------------------------------------------------------------------

surface_app = typer.Typer(help="skos surface: runtime adapters over the brain")
app.add_typer(surface_app, name="surface")


def _make_surface(name: str, root: str):
    """Resolve a Surface by name, mapping --root to the right ctor kwarg."""
    from skos.interface import get_surface, SURFACES
    from skos.adapter import AdapterError
    from pathlib import Path

    kwargs: dict = {}
    if root:
        rpath = Path(root).expanduser()
        if name == "claude-code":
            kwargs["wiki_root"] = rpath
        else:
            kwargs["vault_root"] = rpath
    try:
        return get_surface(name, **kwargs)
    except AdapterError as exc:
        avail = ", ".join(sorted(SURFACES))
        typer.echo(f"error: {exc} (available: {avail})", err=True)
        raise typer.Exit(2)


@surface_app.command("list")
def surface_list_cmd():
    """List registered runtime-adapter surfaces and their status."""
    from skos.interface import SURFACES
    for name in sorted(SURFACES):
        caps = SURFACES[name]().capabilities()
        status = "planned" if caps.planned else "ready"
        typer.echo(f"  {name:<12} {status}")


@surface_app.command("ls")
def surface_ls_cmd(
    name: str = typer.Argument(..., help="Surface name: obsidian | claude-code | codex | n8n"),
    root: str = typer.Option("", "--root", help="Vault/wiki root override"),
):
    """List the entity ids visible on a surface."""
    from skos.interface.base import SurfaceError
    surface = _make_surface(name, root)
    try:
        for node_id in surface.list():
            typer.echo(node_id)
    except SurfaceError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)


@surface_app.command("read")
def surface_read_cmd(
    name: str = typer.Argument(..., help="Surface name"),
    node_id: str = typer.Argument(..., help="Entity node id to read"),
    root: str = typer.Option("", "--root", help="Vault/wiki root override"),
):
    """Read an entity node from a surface and print its rendered markdown."""
    from skos.brain.entity import render
    from skos.interface.base import SurfaceError
    surface = _make_surface(name, root)
    try:
        node = surface.read(node_id)
    except SurfaceError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(render(node))


@surface_app.command("write")
def surface_write_cmd(
    name: str = typer.Argument(..., help="Surface name"),
    file: str = typer.Argument(..., help="Path to an entity node .md file to write"),
    root: str = typer.Option("", "--root", help="Vault/wiki root override"),
):
    """Write an entity node (.md file) onto a surface."""
    from pathlib import Path
    from skos.brain.entity import parse, ParseError
    from skos.interface.base import SurfaceError

    p = Path(file).expanduser()
    if not p.exists():
        typer.echo(f"error: file not found: {p}", err=True)
        raise typer.Exit(1)
    try:
        node = parse(p.read_text(encoding="utf-8"))
    except ParseError as exc:
        typer.echo(f"error: invalid entity node: {exc}", err=True)
        raise typer.Exit(1)

    surface = _make_surface(name, root)
    try:
        surface.write(node)
    except SurfaceError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(f"wrote {node.id} via {name}")


def _split(ref: str):
    scope, _, key = ref.partition("/")
    if not key:
        raise typer.BadParameter("use scope/key, e.g. cloud/cf_token")
    return scope, key


autopilot_app = typer.Typer(help="skos autopilot - autonomous assess/triage/swarm/grade/report")
app.add_typer(autopilot_app, name="autopilot")


@autopilot_app.command("run")
def autopilot_run(
    once: bool = typer.Option(True, "--once/--no-once"),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run",
                                 help="Read-only: no coord/GTD writes, no merges, no DM"),
    canary: bool = typer.Option(False, "--canary"),
    task: str = typer.Option(None, "--task"),
    tasks: str = typer.Option(None, "--tasks",
                              help="Comma-separated card ids: build exactly this BATCH, "
                                   "concurrently on the autoscaled pool"),
    tag: str = typer.Option(None, "--tag",
                            help="Build only unblocked cards carrying this tag "
                                 "(e.g. autopilot, or a per-node assignment tag)"),
    harness: str = typer.Option("stub", "--harness"),
):
    """Execute one autopilot pass (v1: dry-run only, posture C)."""
    from skos.autopilot import orchestrator
    batch = [t.strip() for t in tasks.split(",") if t.strip()] if tasks else None
    out = orchestrator.run_cli(dry_run=dry_run, canary=canary, task=task,
                               tasks=batch, tag=tag, harness=harness)
    typer.echo(out.get("disabled") or f"run {out.get('run_id', '?')} dry_run={dry_run}")


@autopilot_app.command("cleanup")
def autopilot_cleanup(
    teardown: bool = typer.Option(False, "--teardown",
                                  help="also remove the sandbox images (full reclaim; "
                                       "next run rebuilds them). Default keeps them "
                                       "(cold harness, ready to go)."),
):
    """Spin down: reclaim transient build artifacts (exited sandbox containers +
    networks, dead worktree registrations). --teardown also deletes the sandbox
    images. Running builds are never touched."""
    from skharness.autocode import cleanup
    from skharness.autocode.config import Config

    cfg = Config.load()
    repo_paths = [r.path for r in cfg.repo_map.values()]
    out = cleanup.reclaim("teardown" if teardown else "cold", repo_paths=repo_paths)
    typer.echo(out)


@autopilot_app.command("answer")
def autopilot_answer(n: int = typer.Argument(...), response: str = typer.Argument(None)):
    """Resolve numbered decision N."""
    from skos.autopilot import resolver
    try:
        out = resolver.answer(n, response)
    except resolver.UnknownDecision as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(f"answered {out['n']} ({out['qid']}): {out.get('answer')}")


@autopilot_app.command("list")
def autopilot_list(decisions: bool = typer.Option(False, "--decisions"),
                   runs: bool = typer.Option(False, "--runs"),
                   claims: bool = typer.Option(False, "--claims")):
    """List the decision queue, recent runs, or current claims."""
    from skos.autopilot import journal
    what = "decisions" if decisions else "runs" if runs else "claims" if claims else "decisions"
    for line in journal.render_list(what):
        typer.echo(line)


@autopilot_app.command("doctor")
def autopilot_doctor():
    """Self-check the harness: shim delegation, auth, sandbox image, decline rate.

    Catches the failure modes that silently stall live runs (a node still on
    pre-extraction code, a sandbox image built before the current module path, an
    expired token) so they surface in seconds instead of a wasted coding round."""
    from skharness.autocode import doctor
    results = doctor.preflight()
    typer.echo(doctor.format_report(results))
    if any(r.status == "fail" for r in results):
        raise typer.Exit(1)


@autopilot_app.command("status")
def autopilot_status():
    """Render the latest run from the journal."""
    from skos.autopilot import journal
    typer.echo(journal.render_status())


@autopilot_app.command("show")
def autopilot_show(run_id: str = typer.Argument(...)):
    """Dump one run's per-item trajectory."""
    from skos.autopilot import journal
    typer.echo(journal.render_run(run_id))


@autopilot_app.command("revert")
def autopilot_revert(task_id: str = typer.Argument(...)):
    """Revert the recorded merge commit and reopen the coord task."""
    from skos.autopilot import engineering
    out = engineering.revert(task_id)
    typer.echo(f"reverted {task_id}: {out}")


@autopilot_app.command("send")
def autopilot_send(preview: bool = typer.Option(False, "--preview")):
    """Rebuild and send (or preview) the current numbered digest DM."""
    from skos.autopilot import config as _cfg, digest
    if preview:
        typer.echo(digest.build_digest_text(digest.rebuild_manifest()))
        return
    out = digest.send_digest(_cfg.Config.load(), dry_run=False)
    typer.echo(f"digest sent={out.get('sent')} items={out.get('items')}")


@secret_app.command("set")
def secret_set(ref: str, value: str):
    from skos import secrets
    s, k = _split(ref)
    secrets.get_backend().set(s, k, value)
    typer.echo(f"stored {ref}")


@secret_app.command("get")
def secret_get(ref: str):
    from skos import secrets
    s, k = _split(ref)
    typer.echo(secrets.get_backend().get(s, k))


@secret_app.command("list")
def secret_list(scope: str = ""):
    from skos import secrets
    for k in secrets.get_backend().list(scope or None):
        typer.echo(k)


@secret_app.command("rm")
def secret_rm(ref: str):
    from skos import secrets
    s, k = _split(ref)
    secrets.get_backend().delete(s, k)
    typer.echo(f"deleted {ref}")


# ── skbackup: point-in-time backups of the durable skos state ────────────────
backup_app = typer.Typer(
    help="skbackup: point-in-time snapshots of the GTD store, cron ledger, and model registry"
)
app.add_typer(backup_app, name="backup")


@backup_app.command("run")
def backup_run(
    dest: str = typer.Option("", "--dest", help="Local retention dir (default: $SK_BACKUP_DIR or ~/.skcapstone/backups/skos)"),
    keep: int = typer.Option(7, "--keep", help="Retain the N newest snapshots; older are pruned"),
    offbox: str = typer.Option("", "--offbox", help="Off-box target: host:path (rsync) or a local/mounted dir (copy)"),
):
    """Take a consistent snapshot (under the GTD store lock), self-verify, rotate
    to --keep, and optionally copy off-box. This is what the scheduled unit runs."""
    from skos import backup as _backup
    res = _backup.run_backup(dest or None, keep=keep, offbox=offbox or None)
    typer.echo(f"snapshot {res.snapshot}")
    typer.echo(f"verify   {'OK' if res.verify.get('ok') else 'FAILED'} ({res.verify.get('checked', 0)} files)")
    if res.offbox:
        typer.echo(f"offbox   {'OK' if res.offbox_ok else 'FAILED'} -> {res.offbox}")
    if res.rotated:
        typer.echo(f"rotated  pruned {len(res.rotated)} old snapshot(s)")
    if not res.verify.get("ok"):
        for err in res.verify.get("errors", []):
            typer.echo(f"  ! {err}", err=True)
        raise typer.Exit(1)


@backup_app.command("list")
def backup_list(
    dest: str = typer.Option("", "--dest", help="Retention dir to list"),
):
    """List retained snapshots (oldest first)."""
    from skos import backup as _backup
    snaps = _backup.list_snapshots(dest or None)
    if not snaps:
        typer.echo("(no snapshots)")
        return
    for p in snaps:
        typer.echo(f"{p.stat().st_size:>10}  {p.name}")


@backup_app.command("verify")
def backup_verify(snapshot: str = typer.Argument(..., help="Path to a snapshot .tar.gz")):
    """Verify a snapshot's tar + every manifested sha256."""
    from skos import backup as _backup
    res = _backup.verify(snapshot)
    typer.echo(f"{'OK' if res['ok'] else 'FAILED'}: {res['checked']} files checked")
    for err in res["errors"]:
        typer.echo(f"  ! {err}", err=True)
    if not res["ok"]:
        raise typer.Exit(1)


@backup_app.command("restore")
def backup_restore(
    snapshot: str = typer.Argument(..., help="Path to a snapshot .tar.gz"),
    target: str = typer.Argument(..., help="STAGING dir to extract into (never the live paths)"),
):
    """Extract a snapshot's payload into a staging dir for a restore drill.
    Diff the staged tree against live before copying anything back (see runbook)."""
    from skos import backup as _backup
    restored = _backup.restore(snapshot, target)
    typer.echo(f"restored {len(restored)} file(s) to {target}")
    typer.echo("Staged only. Diff against live, then copy back per docs/runbooks/skbackup-restore.md")


# ── revert drill: prove an applied change can be rolled back ──────────────────
revert_drill_app = typer.Typer(
    help="revert-drill: prove a change applied to durable skos state can be rolled back to baseline"
)
app.add_typer(revert_drill_app, name="revert-drill")


@revert_drill_app.command("run")
def revert_drill_run(
    scratch: str = typer.Option("", "--scratch", help="Scratch dir for the drill (default: a temp dir; never live state)"),
):
    """Run a self-contained revert drill: seed a scratch target, snapshot it
    (baseline), apply a change, revert, and assert a byte-for-byte return to
    baseline. Touches ONLY the scratch dir, never live state. Exits non-zero if
    the target did not return to baseline."""
    import tempfile

    from skos import revert_drill as _drill
    scratch_dir = scratch or tempfile.mkdtemp(prefix="skos-revert-drill-")
    res = _drill.run_drill(scratch_dir)
    typer.echo(f"scratch  {scratch_dir}")
    typer.echo(f"baseline {res.baseline_files} file(s)")
    typer.echo(f"revert   restored {len(res.reverted['restored'])}, removed {len(res.reverted['removed'])}")
    typer.echo(f"result   {'OK: target returned to baseline' if res.ok else 'FAILED'}")
    if not res.ok:
        for m in res.mismatches:
            typer.echo(f"  ! changed not restored: {m}", err=True)
        for u in res.unexpected:
            typer.echo(f"  ! addition not removed: {u}", err=True)
        raise typer.Exit(1)


schedule_app = typer.Typer(
    help="Scheduler-as-code: the gtd-ingest / observability cron pipeline, declared "
    "in deploy/schedule/jobs.yaml and installed into the user crontab"
)
app.add_typer(schedule_app, name="schedule")


def _load_schedule(manifest: str):
    from skos import schedule as _sched
    try:
        s = _sched.load(manifest or None)
        _sched.check_runner_exists(s)
        return s
    except _sched.ScheduleError as e:
        typer.echo(f"schedule error: {e}", err=True)
        raise typer.Exit(2)


@schedule_app.command("list")
def schedule_list(
    manifest: str = typer.Option("", "--manifest", help="Path to jobs.yaml (default: repo deploy/schedule/jobs.yaml)"),
):
    """List the declared jobs (name, schedule) after validating the manifest."""
    s = _load_schedule(manifest)
    for job in s.jobs:
        typer.echo(f"{job.schedule:<12}  {job.name}")
    typer.echo(f"({len(s.jobs)} jobs)")


@schedule_app.command("render")
def schedule_render(
    manifest: str = typer.Option("", "--manifest", help="Path to jobs.yaml"),
    expand: bool = typer.Option(False, "--expand", help="Host-concrete paths (still no secret values)"),
    block: bool = typer.Option(False, "--block", help="Wrap in the managed BEGIN/END markers"),
):
    """Print the rendered crontab lines. Secrets stay as $NAME references; no secret
    value is ever emitted by render."""
    from skos import schedule as _sched
    s = _load_schedule(manifest)
    if block:
        typer.echo(_sched.render_block(s, expand=expand))
    else:
        for ln in _sched.render_lines(s, expand=expand):
            typer.echo(ln)


@schedule_app.command("diff")
def schedule_diff(
    manifest: str = typer.Option("", "--manifest", help="Path to jobs.yaml"),
):
    """Diff the manifest against the live user crontab (keyed by job name; secret
    values ignored). Exit 0 = clean, exit 1 = drift."""
    from skos import schedule as _sched
    s = _load_schedule(manifest)
    d = _sched.diff(s, _sched.read_crontab())
    if d.ok and not d.extra:
        typer.echo("clean: live crontab matches the manifest")
        return
    for name in d.missing:
        typer.echo(f"  missing (declared, not live): {name}")
    for name in d.changed:
        typer.echo(f"  changed (schedule/command differs): {name}")
    for name in d.extra:
        typer.echo(f"  extra   (managed live, not declared): {name}")
    if not d.ok:
        raise typer.Exit(1)


@schedule_app.command("install")
def schedule_install(
    manifest: str = typer.Option("", "--manifest", help="Path to jobs.yaml"),
    env_file: str = typer.Option("", "--env-file", help=f"Secret env file (default: {'~/.skcapstone/skos-schedule.env'})"),
    apply: bool = typer.Option(False, "--apply", help="Actually write the crontab (default: dry-run preview)"),
):
    """Render the managed block (secrets injected from the env file) and splice it
    into the user crontab. Default is a DRY RUN that prints the resulting crontab;
    pass --apply to write it."""
    from skos import schedule as _sched
    s = _load_schedule(manifest)
    try:
        new_text = _sched.install(s, env_file=env_file or None, dry_run=not apply)
    except _sched.ScheduleError as e:
        typer.echo(f"install error: {e}", err=True)
        raise typer.Exit(2)
    if apply:
        typer.echo(f"installed: {len(s.jobs)} managed job(s) written to the user crontab")
        d = _sched.diff(s, _sched.read_crontab())
        typer.echo("post-install diff: " + ("clean" if d.ok else "DRIFT (see `skos schedule diff`)"))
    else:
        typer.echo("# DRY RUN - resulting crontab (pass --apply to write):")
        typer.echo(new_text)


# ── cold-start bootstrap: empty-store guard + node sentinel ───────────────────
coldstart_app = typer.Typer(
    help="Cold-start bootstrap: empty-store guard + node-initialized sentinel (restore before first run)"
)
app.add_typer(coldstart_app, name="coldstart")


@coldstart_app.command("check")
def coldstart_check(
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Report the cold-start decision for this node WITHOUT emitting anything.
    Exits non-zero when the empty-store guard would trip (initialized node,
    empty store) so a preflight step can gate services before they run."""
    from skos import coldstart as _cs
    r = _cs.evaluate()
    if json_out:
        import json as _json
        typer.echo(_json.dumps(r.__dict__, indent=2))
    else:
        typer.echo(f"initialized  {r.initialized}")
        typer.echo(f"store_empty  {r.store_empty} ({r.item_count} item(s))")
        typer.echo(f"override     {r.override}")
        typer.echo(f"store_dir    {r.store_dir}")
        typer.echo(f"marker       {r.marker}")
        typer.echo(f"verdict      {'GUARD WOULD TRIP' if r.would_trip else 'ok to emit'}")
        typer.echo(f"reason       {r.reason}")
    if r.would_trip:
        raise typer.Exit(1)


@coldstart_app.command("init")
def coldstart_init(
    force: bool = typer.Option(False, "--force", help="Stamp even if the store is currently empty"),
):
    """Stamp this node as initialized (run AFTER the store is restored/synced).
    Refuses on an empty store unless --force, so you do not mark a node as
    initialized before its data is in place (which would then trip the guard)."""
    from skos import coldstart as _cs
    if _cs.store_is_empty() and not force:
        typer.echo(
            "refusing: store is EMPTY. Restore/sync the GTD store first, then "
            "`skos coldstart init` (or pass --force for a deliberately empty node).",
            err=True,
        )
        raise typer.Exit(1)
    p = _cs.mark_initialized()
    typer.echo(f"node-initialized: {p}")


# ── skos secrets: blank-machine secret provisioning + recovery (card d65ff0ca) ─
secrets_app = typer.Typer(
    help="Secret PLANE provisioning/recovery: read-only status + operator env scaffold "
    "(the credentials the guarded services need on a blank machine)"
)
app.add_typer(secrets_app, name="secrets")


@secrets_app.command("check")
def secrets_check(
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Report which secrets are present vs missing on THIS machine, read-only.
    Never prints a secret value. Exits non-zero when a REQUIRED plane credential
    (vault master key, operator env file, gog keyring password) is missing, so a
    blank-machine preflight can gate services. Recovery order is in
    docs/runbooks/skos-secret-provisioning.md."""
    from skos import secrets_check as _sc
    report = _sc.check()
    if json_out:
        import json as _json
        typer.echo(_json.dumps(report.as_dict(), indent=2))
    else:
        for s in report.statuses:
            mark = "ok  " if s.present else ("MISS" if s.required else "opt ")
            tag = "required" if s.required else "optional"
            typer.echo(f"[{mark}] {s.name:<20} ({tag}, {s.kind})")
            typer.echo(f"        where : {s.where}")
            typer.echo(f"        source: {s.source}")
            typer.echo(f"        status: {s.detail}")
        typer.echo("")
        if report.ok:
            typer.echo("verdict  all required secrets present")
        else:
            miss = ", ".join(s.name for s in report.missing_required)
            typer.echo(f"verdict  MISSING required: {miss}")
    if not report.ok:
        raise typer.Exit(1)


@secrets_app.command("bootstrap")
def secrets_bootstrap(
    force: bool = typer.Option(False, "--force", help="Overwrite an existing env file"),
):
    """Scaffold the operator env file with placeholder keys (mode 600), then print
    the ordered recovery checklist. Writes ONLY placeholders, never a real secret,
    and never clobbers an existing file unless --force."""
    from skos import secrets_check as _sc
    res = _sc.bootstrap(force=force)
    typer.echo(f"env file  {res.path} ({'created' if res.created else 'unchanged'})")
    typer.echo(f"          {res.detail}")
    typer.echo("")
    typer.echo("recovery order (see docs/runbooks/skos-secret-provisioning.md):")
    typer.echo("  1. master.key  <- restore from escrow/skvault to $SK_DATA_ROOT/secrets/ (mode 600)")
    typer.echo("  2. env file    <- fill placeholders above from skvault/escrow, then chmod 600")
    typer.echo("  3. gog tokens  <- re-auth via the gmail-oauth skill (GOG_KEYRING_PASSWORD unlocks them)")
    typer.echo("  4. capauth     <- provision identity via the capauth agent / skvault (optional)")
    typer.echo("  5. verify      <- `skos secrets check` should exit 0")


operator_app = typer.Typer(
    help="skos operator facet: the explain / observe / act contract (R2.12-style). "
    "The canonical CLI that Atlas's skos adapter mirrors."
)
app.add_typer(operator_app, name="operator")


@operator_app.command("explain")
def operator_explain():
    """Print the operator-facet contract (kinds/conditions/actions) as JSON."""
    import json as _json
    from skos.operator_probe import explain as _explain
    typer.echo(_json.dumps(_explain(), indent=2))


@operator_app.command("observe")
def operator_observe():
    """Print live operator conditions as JSON from real probes (each fails safe =
    healthy when skos is unreachable). SchedulerAlive reads the cron run-ledger;
    GtdSinkDraining reads the GTD sink's quarantine backlog."""
    import json as _json
    from skos.operator_probe import observe as _observe
    typer.echo(_json.dumps(_observe(), indent=2))


@operator_app.command("act")
def operator_act(
    action: str = typer.Argument(..., help="restart_service | replay_errors"),
    unit: str = typer.Option("", "--unit", help="Override the systemd unit for restart_service."),
):
    """Perform a reversible standard action, or refuse.

    ACTION is one of restart_service (systemctl --user restart the skscheduler
    unit) or replay_errors (skos gtd replay-errors, draining the error-recovery
    queue). Both are standard, reversible, low blast. An unknown action is
    refused; any non-standard action escalates as MAJOR and never actuates."""
    import json as _json
    from skos.operator_probe import act as _act
    try:
        result = _act(action, unit=unit or None)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    typer.echo(_json.dumps(result, indent=2))


manifest_app = typer.Typer(
    help="skos SKWorld module manifest: emit the static file the umbrella shell "
    "registry reads (spec 5.3 local-file location)."
)
app.add_typer(manifest_app, name="manifest")


@manifest_app.command("emit")
def manifest_emit(
    base_url: str = typer.Option(
        _skworld_manifest.DEFAULT_BASE_URL,
        "--base-url",
        help="Serving origin baked into the manifest's origin-relative URLs. "
        "skos has no web server yet, so the shell interim-routes this entry to the "
        "native skos screens (spec 4.4); pass the real origin once skos' web UI lands.",
    ),
    out: str = typer.Option(
        "", "--out", help="Output path (default: the shell well-known location)."
    ),
    show: bool = typer.Option(
        False, "--print", help="Print the manifest JSON to stdout; write nothing."
    ),
):
    """Emit skos' skworld.module.json as a deterministic static file for the umbrella
    shell registry.

    skos is a CLI + scheduler with no HTTP surface, so it publishes its manifest as a
    signed LOCAL FILE (umbrella spec 5.3 "local file" location) rather than serving
    /.well-known/skworld-module.json from a daemon. The emitted bytes are deterministic
    (sorted keys) so re-emitting an unchanged manifest is a no-op diff and its capauth
    signature is reproducible.

    After emit: attach a detached capauth signature and register the file path in
    ~/.skcapstone/shell/modules.json. The shell refuses any manifest whose signature
    does not verify (spec 5.3). See docs/runbooks/skos-manifest.md.
    """
    if show:
        typer.echo(_skworld_manifest.render_manifest_json(base_url), nl=False)
        return
    path = _skworld_manifest.emit_manifest_file(base_url, out or None)
    typer.echo(f"manifest  {path}")
    typer.echo(
        f"          schemaVersion {_skworld_manifest.SCHEMA_VERSION}, id=skos, "
        f"base_url={base_url}"
    )
    typer.echo("")
    typer.echo("next (shell registration, see docs/runbooks/skos-manifest.md):")
    typer.echo(
        f"  1. sign     <- attach a detached capauth signature ({path.name}.sig) "
        "with the operator key"
    )
    typer.echo(
        "  2. register <- add this file path to ~/.skcapstone/shell/modules.json "
        "(local-file entry) + the enable set"
    )
    typer.echo(
        "  3. verify   <- the shell refuses any manifest whose signature does not "
        "verify (spec 5.3)"
    )
