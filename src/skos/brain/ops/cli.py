"""The skbrain operational knowledge CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from skos.brain.ops.doctor import checks_json, run_checks
from skos.brain.ops.embed import MxbaiEmbedder
from skos.brain.ops.parser import walk_pages
from skos.brain.ops.postgres import PostgresWriterBackend, dsn_from_env
from skos.brain.ops.read_api import OpsReader
from skos.brain.ops.secrets import lint_tree
from skos.brain.ops.writer import project

app = typer.Typer(help="Project and retrieve the private operations brain.")
operator_app = typer.Typer(help="ATLAS out-of-process operator contract.")
app.add_typer(operator_app, name="operator")


def _canon(path: Path | None) -> Path:
    return (path or Path(os.environ.get("SKBRAIN_CANON", "~/clawd/skbrain-ops"))).expanduser()


@app.command("lint")
def lint(canon: Path | None = typer.Option(None)) -> None:
    """Fail when private canon contains secret-shaped material."""
    findings = lint_tree(_canon(canon))
    typer.echo(json.dumps([f.__dict__ for f in findings], sort_keys=True))
    if findings:
        raise typer.Exit(2)


@app.command("sync")
def sync(canon: Path | None = typer.Option(None), commit: bool = typer.Option(False, "--commit"),
         ) -> None:
    """Lint, parse, and atomically project canon; defaults to dry-run."""
    root = _canon(canon)
    findings = lint_tree(root)
    if findings:
        typer.echo(json.dumps({"ok": False, "secret_findings": len(findings)}))
        raise typer.Exit(2)
    pages = walk_pages(root)
    with PostgresWriterBackend(dsn_from_env("SKBRAIN_PG_PROJECTOR_DSN")) as backend:
        # A dry run remains entirely local + DB read-only. A committed projection
        # must embed; storing NULL then content-hash-skipping forever is unsafe.
        result = project(pages, backend, embedder=MxbaiEmbedder() if commit else None, commit=commit)
    typer.echo(json.dumps(result.__dict__, sort_keys=True))


@app.command("search")
def search(query: str, limit: int = typer.Option(8, min=1, max=25), kind: str | None = None) -> None:
    """Search the private ops namespace using the reader identity."""
    from dataclasses import asdict
    hits = OpsReader(dsn_from_env("SKBRAIN_PG_READER_DSN")).search(query, limit=limit, kind=kind)
    typer.echo(json.dumps([asdict(hit) for hit in hits], sort_keys=True))


@app.command("doctor")
def doctor(canon: Path | None = typer.Option(None)) -> None:
    """Run non-mutating installation and health checks."""
    checks = run_checks(canon=_canon(canon), reader_dsn=os.environ.get("SKBRAIN_PG_READER_DSN"))
    typer.echo(json.dumps({"ok": all(c.ok for c in checks), "checks": checks_json(checks)}, sort_keys=True))
    if not all(c.ok for c in checks):
        raise typer.Exit(1)


@operator_app.command("explain")
def operator_explain(json_output: bool = typer.Option(False, "--json")) -> None:
    """Describe the bounded ATLAS surface."""
    payload = {"application": "skbrain", "read_only": True,
               "conditions": ["OpsSchemaPresent", "ProjectorFresh", "KedbCanonCovered"]}
    typer.echo(json.dumps(payload, sort_keys=True) if json_output else str(payload))


@operator_app.command("observe")
def operator_observe(json_output: bool = typer.Option(False, "--json"),
                     canon: Path | None = typer.Option(None)) -> None:
    """Emit fail-closed ATLAS conditions from doctor evidence."""
    checks = run_checks(canon=_canon(canon), reader_dsn=os.environ.get("SKBRAIN_PG_READER_DSN"))
    by_name = {c.name: c for c in checks}
    def condition(name: str, checks_: tuple[str, ...]) -> dict[str, object]:
        relevant = [by_name[x] for x in checks_ if x in by_name]
        return {"type": name, "status": "True" if relevant and all(x.ok for x in relevant) else "Unknown",
                "reason": "; ".join(x.detail for x in relevant) or "evidence unavailable"}
    payload = {"application": "skbrain", "conditions": [
        condition("OpsSchemaPresent", ("skbrain:schema", "skbrain:grants")),
        condition("ProjectorFresh", ("skbrain:projector",)),
        {"type": "CmdbDriftBounded", "status": "Unknown", "reason": "CMDB evidence is owned by the CMDB adapter"},
        {"type": "KedbCanonCovered", "status": "Unknown", "reason": "authoritative KEDB fold is unavailable to this read-only adapter"},
    ]}
    typer.echo(json.dumps(payload, sort_keys=True) if json_output else str(payload))


@operator_app.command("act")
def operator_act(json_output: bool = typer.Option(False, "--json")) -> None:
    """Refuse physical action; ATLAS must use a separately ratified adapter."""
    payload = {"performed": False, "reason": "skbrain operator facet is observation-only"}
    typer.echo(json.dumps(payload, sort_keys=True) if json_output else str(payload))
    raise typer.Exit(3)
