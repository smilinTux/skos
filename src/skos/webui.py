"""skos' minimal, READ-ONLY web surface (SKWorld Grade B embed pane).

skos is a CLI + scheduler with no long-lived daemon. This module gives it an
OPTIONAL, dependency-light HTTP surface so the SKWorld umbrella shell can:

  * discover skos live at ``GET /.well-known/skworld-module.json`` (the same
    manifest ``skos manifest emit`` writes to a static file, but built
    origin-relative from the actual request so served URLs never drift from the
    host/port the shell reached us on); and
  * mount skos' single-pane-of-glass status surface as a Grade B web embed at
    ``GET /`` (aka ``GET /app``), the web-embed path the umbrella spec assigns
    skos (spec 4.4, "same Grade B path as skdashboard").

Everything here is strictly READ-ONLY. There are no POST/PUT/PATCH/DELETE
routes, no actions, no dispatch, no writes. The status page reads fast local
signals only (the cron run-ledger, the unified GTD store, the operator probe)
and fails SAFE (renders "unknown" rather than raising) so an unreachable signal
never takes the pane down. Slow external calls (gog mail, psql corpus counts)
are deliberately excluded so the embed stays snappy.

FastAPI + uvicorn are an OPTIONAL extra (``skos[web]``); they are imported lazily
inside :func:`build_app` / :func:`run` so importing this module (or running the
CLI/scheduler without the extra installed) never fails.

Bind loopback by default (``127.0.0.1``); never a public wildcard. Override the
host/port with ``--host`` / ``--port`` or ``SKOS_WEB_HOST`` / ``SKOS_WEB_PORT``.
"""

import html
import os
import socket
from typing import Any

from skos import skworld_manifest as _manifest

#: Default bind host: loopback only. The shell reaches skos over the tailnet or
#: loopback; we never bind a public wildcard (0.0.0.0) by default.
DEFAULT_HOST = os.environ.get("SKOS_WEB_HOST", "127.0.0.1")
#: Default port. 7781 is free on this fleet (7778=skdashboard, 9384=skcomms,
#: 9386=sk-access, 9394=skcode, 8765/8766=skchat webui). Override via env.
DEFAULT_PORT = int(os.environ.get("SKOS_WEB_PORT", "7781"))


# --- read-only signal gathering (each fails safe) ----------------------------


def _node_health() -> dict[str, Any]:
    """Node identity + data-root resolution. Never raises."""
    info: dict[str, Any] = {}
    try:
        info["host"] = socket.gethostname()
    except Exception:
        info["host"] = "unknown"
    try:
        from skos import profile as _profile

        info["profile"] = _profile.active().value
    except Exception:
        info["profile"] = "unknown"
    try:
        from skos import paths

        info["data_root"] = str(paths.data_root())
    except Exception:
        info["data_root"] = "unset"
    return info


def _scheduler_health() -> dict[str, Any]:
    """Scheduler + GTD-sink conditions from the operator probe. Fails safe."""
    try:
        from skos import operator_probe

        probe = operator_probe._default_probe()
        return {
            "scheduler_alive": bool(probe.get("scheduler_alive", True)),
            "gtd_draining": bool(probe.get("gtd_draining", True)),
            "quarantine_depth": int(probe.get("quarantine_depth", 0)),
        }
    except Exception:
        return {"scheduler_alive": None, "gtd_draining": None, "quarantine_depth": None}


def _gtd_counts() -> dict[str, Any]:
    """Unified GTD store counts (fast local JSON reads). Fails safe to {}."""
    try:
        from skos.status import gtd_status

        return gtd_status()
    except Exception:
        return {}


def _recent_jobs() -> list[dict[str, Any]]:
    """Recent cron/scheduled job runs (last 24h, newest first). Fails safe to []."""
    try:
        from skos.status import cron_status

        jobs = cron_status()
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for name, v in jobs.items():
        rows.append(
            {
                "job": name,
                "runs": v.get("runs", 0),
                "ok": v.get("ok", 0),
                "fail": v.get("fail", 0),
                "last": (v.get("last") or "")[:16],
                "last_ok": bool(v.get("last_ok")),
                "last_dur": v.get("last_dur"),
            }
        )
    rows.sort(key=lambda r: r["last"], reverse=True)
    return rows


def status_snapshot() -> dict[str, Any]:
    """The full read-only status snapshot the page and /status.json both render."""
    return {
        "service": "skos",
        "node": _node_health(),
        "scheduler": _scheduler_health(),
        "gtd": _gtd_counts(),
        "jobs": _recent_jobs(),
    }


# --- HTML rendering (self-contained, CSP-safe: no external CDN/fonts/JS) ------


def _dot(ok: Any) -> str:
    """A status glyph: green when True, red when False, grey when unknown."""
    if ok is True:
        return '<span class="dot ok">&#9679;</span>'
    if ok is False:
        return '<span class="dot bad">&#9679;</span>'
    return '<span class="dot unk">&#9679;</span>'


def _esc(v: Any) -> str:
    return html.escape(str(v), quote=True)


def render_status_html(snap: dict[str, Any]) -> str:
    """Render the read-only status page. Self-contained, no external assets."""
    node = snap.get("node", {})
    sched = snap.get("scheduler", {})
    gtd = snap.get("gtd", {})
    jobs = snap.get("jobs", [])

    q = sched.get("quarantine_depth")
    q_txt = "n/a" if q is None else str(q)

    job_rows = []
    for j in jobs[:25]:
        mark = _dot(j["last_ok"] if j["runs"] else None)
        dur = "" if j.get("last_dur") is None else f"{_esc(j['last_dur'])}s"
        job_rows.append(
            "<tr>"
            f"<td>{mark}</td>"
            f"<td class=\"mono\">{_esc(j['job'])}</td>"
            f"<td class=\"num\">{_esc(j['runs'])}</td>"
            f"<td class=\"num ok\">{_esc(j['ok'])}</td>"
            f"<td class=\"num bad\">{_esc(j['fail'])}</td>"
            f"<td class=\"mono dim\">{_esc(j['last'])}</td>"
            f"<td class=\"num dim\">{dur}</td>"
            "</tr>"
        )
    jobs_body = (
        "\n".join(job_rows)
        if job_rows
        else '<tr><td colspan="7" class="dim">no runs recorded in the last 24h</td></tr>'
    )

    def gc(name: str) -> str:
        v = gtd.get(name)
        return "?" if v is None else _esc(v)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>skos status</title>
<style>
:root {{
  --bg:#0b0d10; --panel:#14181d; --line:#232a31; --fg:#dfe6ee; --dim:#7c8896;
  --ok:#4ade80; --bad:#f87171; --unk:#5a6572; --accent:#60a5fa;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg);
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:14px; }}
header {{ padding:14px 18px; border-bottom:1px solid var(--line);
  display:flex; align-items:baseline; gap:12px; }}
header h1 {{ font-size:16px; margin:0; letter-spacing:.5px; }}
header .badge {{ font-size:11px; color:var(--dim); border:1px solid var(--line);
  padding:2px 7px; border-radius:10px; }}
main {{ padding:16px 18px; display:grid; gap:16px;
  grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); }}
.panel {{ background:var(--panel); border:1px solid var(--line); border-radius:8px;
  padding:14px 16px; }}
.panel h2 {{ font-size:12px; text-transform:uppercase; letter-spacing:1px;
  color:var(--dim); margin:0 0 10px; }}
.kv {{ display:flex; justify-content:space-between; padding:3px 0; }}
.kv .k {{ color:var(--dim); }}
.dot {{ font-size:11px; vertical-align:middle; }}
.dot.ok {{ color:var(--ok); }} .dot.bad {{ color:var(--bad); }} .dot.unk {{ color:var(--unk); }}
.ok {{ color:var(--ok); }} .bad {{ color:var(--bad); }} .dim {{ color:var(--dim); }}
.mono {{ font-family:inherit; }}
table {{ width:100%; border-collapse:collapse; }}
.jobs-wrap {{ overflow-x:auto; }}
th, td {{ text-align:left; padding:5px 8px; border-bottom:1px solid var(--line);
  white-space:nowrap; }}
th {{ color:var(--dim); font-weight:normal; font-size:11px; text-transform:uppercase; }}
td.num {{ text-align:right; }}
.grid2 {{ grid-column:1 / -1; }}
footer {{ padding:10px 18px 20px; color:var(--dim); font-size:11px; }}
</style>
</head>
<body>
<header>
  <h1>skos</h1>
  <span class="badge">read-only status</span>
  <span class="badge">SKWorld module &middot; Grade B</span>
</header>
<main>
  <section class="panel">
    <h2>Node</h2>
    <div class="kv"><span class="k">host</span><span>{_esc(node.get('host', '?'))}</span></div>
    <div class="kv"><span class="k">profile</span><span>{_esc(node.get('profile', '?'))}</span></div>
    <div class="kv"><span class="k">data root</span><span class="mono">{_esc(node.get('data_root', '?'))}</span></div>
  </section>
  <section class="panel">
    <h2>Scheduler &amp; GTD sink</h2>
    <div class="kv"><span class="k">scheduler alive</span><span>{_dot(sched.get('scheduler_alive'))}</span></div>
    <div class="kv"><span class="k">gtd sink draining</span><span>{_dot(sched.get('gtd_draining'))}</span></div>
    <div class="kv"><span class="k">quarantine backlog</span><span>{q_txt}</span></div>
  </section>
  <section class="panel">
    <h2>GTD / ingest counts</h2>
    <div class="kv"><span class="k">inbox</span><span>{gc('inbox')}</span></div>
    <div class="kv"><span class="k">next-actions</span><span>{gc('next-actions')}</span></div>
    <div class="kv"><span class="k">projects</span><span>{gc('projects')}</span></div>
    <div class="kv"><span class="k">waiting-for</span><span>{gc('waiting-for')}</span></div>
    <div class="kv"><span class="k">someday-maybe</span><span>{gc('someday-maybe')}</span></div>
  </section>
  <section class="panel grid2">
    <h2>Recent job runs (last 24h)</h2>
    <div class="jobs-wrap">
    <table>
      <thead><tr><th></th><th>job</th><th>runs</th><th>ok</th><th>fail</th><th>last</th><th>dur</th></tr></thead>
      <tbody>
      {jobs_body}
      </tbody>
    </table>
    </div>
  </section>
</main>
<footer>read-only pane &middot; auto-refreshes every 30s &middot; no actions, no writes</footer>
</body>
</html>"""


# --- app factory + entry point -----------------------------------------------


def build_app():
    """Build the read-only FastAPI app. Imports FastAPI lazily (optional extra)."""
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse

    app = FastAPI(
        title="skos web surface",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/.well-known/skworld-module.json")
    async def skworld_module(request: Request) -> JSONResponse:
        """Serve skos' SKWorld module manifest, built origin-relative from the
        request so the served URLs resolve against wherever the shell reached us."""
        return JSONResponse(_manifest.skos_module_manifest(str(request.base_url)))

    @app.get("/", response_class=HTMLResponse)
    @app.get("/app", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        """The read-only Grade B status pane the umbrella shell embeds."""
        return HTMLResponse(render_status_html(status_snapshot()))

    @app.get("/status.json")
    async def status_json() -> JSONResponse:
        """Machine-readable copy of the same read-only snapshot."""
        return JSONResponse(status_snapshot())

    @app.get("/health")
    async def health() -> JSONResponse:
        """Liveness probe the manifest advertises at ``/health``."""
        sched = _scheduler_health()
        return JSONResponse({"status": "ok", "service": "skos", "scheduler": sched})

    return app


def run(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = False,
) -> None:
    """Start the read-only web surface (blocking). Loopback by default."""
    import uvicorn

    if open_browser:
        import webbrowser

        webbrowser.open(f"http://{host if host not in ('0.0.0.0',) else 'localhost'}:{port}")
    uvicorn.run(build_app(), host=host, port=port, log_level="warning")


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "build_app",
    "run",
    "status_snapshot",
    "render_status_html",
]
