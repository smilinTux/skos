"""skmodels: CLI for the skos model registry (single source of truth).

Subcommands:
  list                          roles + backends + contexts + default
  get <role|context>            what a role or context key resolves to
  resolve [--role R] [--context C] [--service S]
                                print url + model (precedence context>service>role>default)
  set <context-key> <role|backend>
                                the TOGGLE (e.g. skmodels set chat:dr-chiro-group sk-vision)
  test <role>                   curl the backend and report up/down
  rank <role>                   ask the gateway to rank candidates for a registry role
  suggest --need ... --ctx ... --tier ...
                                build an inline require= spec and ask the gateway to rank it

Registry path: $SKMODELS_REGISTRY or ~/.skcapstone/models/registry.yaml
Gateway rank endpoint: $SKGATEWAY_URL (default http://localhost:18780), loopback only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode

from skos.models import (
    Backend,
    load_registry,
    resolve,
    set_context,
    unset_context,
)

GATEWAY_URL_ENV = "SKGATEWAY_URL"
DEFAULT_GATEWAY_BASE = "http://localhost:18780"

# CLI-friendly aliases for the require= spec's boolean keys, e.g.
# `--need tools` -> `tool_use` (the field name the gateway's ranker uses).
NEED_ALIASES = {
    "tools": "tool_use",
    "tool": "tool_use",
}


def _p(*a):
    print(*a)


def _perr(*a):
    print(*a, file=sys.stderr)


def cmd_list(args) -> int:
    reg = load_registry()
    _p(f"registry: {reg.source}")
    _p(f"default role: {reg.default_role}")
    _p("")
    _p("BACKENDS")
    if not reg.backends:
        _p("  (none)")
    for name in reg.backends:
        b = reg.make_backend(name)
        bits = [f"model={b.model}", f"kind={b.kind}"]
        if b.ctx:
            bits.append(f"ctx={b.ctx}")
        if b.vision:
            bits.append("vision")
        if b.dim:
            bits.append(f"dim={b.dim}")
        _p(f"  {name:<12} {b.url}")
        _p(f"  {'':<12} " + "  ".join(bits))
    _p("")
    _p("ROLES")
    if not reg.roles:
        _p("  (none)")
    for role, backend in reg.roles.items():
        _p(f"  {role:<12} -> {backend}")
    _p("")
    _p("CONTEXTS (toggles)")
    if not reg.contexts:
        _p("  (none set, use `skmodels set <key> <role|backend>`)")
    for key, target in reg.contexts.items():
        _p(f"  {key:<28} -> {target}")
    return 0


def cmd_get(args) -> int:
    reg = load_registry()
    key = args.name
    if key in reg.roles:
        _p(f"role   {key} -> backend {reg.roles[key]}")
    elif key in reg.contexts:
        _p(f"context {key} -> {reg.contexts[key]}")
    elif key in reg.backends:
        _p(f"backend {key}")
    else:
        _p(f"unknown role/context/backend: {key}", )
        return 1
    b = reg.resolve(role=key if key in reg.roles else None,
                    context=key if key in reg.contexts else None)
    _p(f"resolves to: {b.name}  url={b.url}  model={b.model}")
    return 0


def cmd_resolve(args) -> int:
    b: Backend = resolve(role=args.role, context=args.context, service=args.service)
    if args.json:
        _p(json.dumps(b.to_dict(), indent=2))
        return 0
    _p(f"backend: {b.name}")
    _p(f"url:     {b.url}")
    _p(f"model:   {b.model}")
    _p(f"kind:    {b.kind}")
    if b.ctx:
        _p(f"ctx:     {b.ctx}")
    _p(f"vision:  {b.vision}")
    if b.dim:
        _p(f"dim:     {b.dim}")
    return 0


def cmd_set(args) -> int:
    p = set_context(args.key, args.target)
    _p(f"set {args.key} -> {args.target}")
    _p(f"written: {p}")
    # show resolution
    b = resolve(context=args.key)
    _p(f"{args.key} now resolves to: {b.name}  {b.url}  {b.model}")
    return 0


def cmd_unset(args) -> int:
    removed = unset_context(args.key)
    if removed:
        b = resolve(context=args.key)
        _p(f"unset {args.key} (reverted to role/default -> {b.name} {b.model})")
    else:
        _p(f"{args.key}: no such context")
    return 0


def _probe(b: Backend, timeout: int = 6) -> tuple[bool, str]:
    """Return (up, detail). Chat -> GET {url}/models. Embed -> GET origin root."""
    if not b.url:
        return False, "no url"
    if b.kind == "embed":
        # embed endpoints (e.g. Ollama /api/embed): probe the /api/tags sibling
        base = b.url.rsplit("/api/", 1)[0] if "/api/" in b.url else b.url.rstrip("/")
        probe = base.rstrip("/") + "/api/tags"
    else:
        probe = b.url.rstrip("/") + "/models"
    try:
        req = urllib.request.Request(probe, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(4096).decode("utf-8", "replace")
        return True, f"{probe} -> HTTP {resp.status}"
    except HTTPError as e:
        # a 4xx/5xx still means the host answered
        return True, f"{probe} -> HTTP {e.code} (reachable)"
    except (URLError, OSError) as e:
        return False, f"{probe} -> {e}"
    except Exception as e:  # pragma: no cover
        return False, f"{probe} -> {e}"


def cmd_test(args) -> int:
    b = resolve(role=args.name, context=args.name)
    up, detail = _probe(b)
    status = "UP" if up else "DOWN"
    _p(f"{args.name}: backend={b.name} model={b.model}")
    _p(f"  {status}  {detail}")
    return 0 if up else 2


def _gateway_base() -> str:
    """Gateway base url (no trailing slash, no /v1 suffix).

    Reuses the same $SKGATEWAY_URL env var as the rest of skos (gtd_triage.py,
    adapters/order.py); those callers hit .../v1/chat/completions, the admin
    rank route is unversioned, so a trailing /v1 is stripped if present.
    """
    raw = os.environ.get(GATEWAY_URL_ENV, DEFAULT_GATEWAY_BASE).rstrip("/")
    if raw.endswith("/v1"):
        raw = raw[: -len("/v1")]
    return raw


def _parse_ctx(value: str) -> int:
    """Parse a context-size flag like '64k', '1m', or '131072' into tokens.

    's' suffix = *1000, matching the design doc's own convention of writing
    "64k" for a min_ctx=64000 requirement (docs/specs/2026-08-08-model-
    ranking-routing-intelligence-arch.md 7.1).
    """
    v = value.strip().lower()
    if v.endswith("k"):
        return int(float(v[:-1]) * 1_000)
    if v.endswith("m"):
        return int(float(v[:-1]) * 1_000_000)
    return int(v)


def _build_require(need: list[str] | None, ctx: str | None, tier: str | None) -> str:
    """Build the require= spec string the gateway's rank endpoint parses.

    Same comma-separated `key` / `key=value` grammar as the x-sk-require
    header (design doc 7.1): tier is a pipe-separated sovereignty ladder.
    """
    parts: list[str] = []
    for group in need or []:
        for item in group.split(","):
            item = item.strip()
            if item:
                parts.append(NEED_ALIASES.get(item, item))
    if ctx:
        parts.append(f"min_ctx={_parse_ctx(ctx)}")
    if tier:
        tiers = "|".join(t.strip() for t in tier.split(",") if t.strip())
        if tiers:
            parts.append(f"tier={tiers}")
    return ",".join(parts)


def _fetch_rank(params: dict[str, str], timeout: int = 10) -> dict:
    """GET {gateway}/admin/models/rank?<params>. Raises RuntimeError with a
    clear, user-facing message on any failure (gateway down, non-2xx, bad
    JSON) so callers can print it and exit non-zero instead of a traceback.
    """
    url = _gateway_base() + "/admin/models/rank"
    qs = urlencode({k: v for k, v in params.items() if v})
    if qs:
        url += "?" + qs
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:  # pragma: no cover
            pass
        raise RuntimeError(f"gateway returned HTTP {e.code} for {url}: {body[:200]}") from e
    except (URLError, OSError) as e:
        raise RuntimeError(f"gateway unreachable at {url}: {e.reason if hasattr(e, 'reason') else e}") from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"gateway returned invalid JSON from {url}: {e}") from e


def _fmt_score(score) -> str:
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        return f"{score:.3f}"
    return str(score)


def _print_rank(data: dict) -> None:
    """Pretty-print a rank response: the ranked chain, then each candidate's
    score/tier and its per-dimension breakdown (with basis tags). Formatting
    only, no ranking logic (that all lives in the gateway).
    """
    role = data.get("role")
    if role:
        _p(f"role: {role}")
    chain = data.get("chain") or data.get("candidates") or []
    if not chain:
        _p("(no candidates ranked)")
        return
    _p("")
    _p("RANKED CHAIN")
    for i, cand in enumerate(chain, 1):
        cid = cand.get("id", "?")
        line = f"  {i}. {cid}"
        if "score" in cand:
            line += f"  score={_fmt_score(cand['score'])}"
        if cand.get("tier"):
            line += f"  tier={cand['tier']}"
        _p(line)
        excluded = cand.get("excluded_reason")
        if excluded:
            _p(f"     excluded: {excluded}")
        breakdown = cand.get("breakdown") or {}
        for dim, info in breakdown.items():
            if isinstance(info, dict):
                val = info.get("value", info.get("score"))
                basis = info.get("basis")
                bit = f"{dim}={val}"
                if basis:
                    bit += f" (basis={basis})"
            else:
                bit = f"{dim}={info}"
            _p(f"     {bit}")


def cmd_rank(args) -> int:
    try:
        data = _fetch_rank({"role": args.role})
    except RuntimeError as e:
        _perr(f"skmodels rank: {e}")
        return 2
    _print_rank(data)
    return 0


def cmd_suggest(args) -> int:
    require = _build_require(args.need, args.ctx, args.tier)
    if not require:
        _perr("skmodels suggest: nothing to suggest on, pass --need/--ctx/--tier")
        return 1
    _p(f"require: {require}")
    try:
        data = _fetch_rank({"require": require})
    except RuntimeError as e:
        _perr(f"skmodels suggest: {e}")
        return 2
    _print_rank(data)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="skmodels", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="roles + backends + contexts").set_defaults(fn=cmd_list)

    g = sub.add_parser("get", help="what a role/context resolves to")
    g.add_argument("name")
    g.set_defaults(fn=cmd_get)

    r = sub.add_parser("resolve", help="resolve to url+model")
    r.add_argument("--role", "-r")
    r.add_argument("--context", "-c")
    r.add_argument("--service", "-s")
    r.add_argument("--json", "-j", action="store_true")
    r.set_defaults(fn=cmd_resolve)

    s = sub.add_parser("set", help="TOGGLE: pin a context to a role/backend")
    s.add_argument("key")
    s.add_argument("target")
    s.set_defaults(fn=cmd_set)

    u = sub.add_parser("unset", help="remove a context toggle (revert to role/default)")
    u.add_argument("key", help="context key, e.g. chat:12345")
    u.set_defaults(fn=cmd_unset)

    t = sub.add_parser("test", help="curl the backend for a role/context")
    t.add_argument("name")
    t.set_defaults(fn=cmd_test)

    rk = sub.add_parser("rank", help="ask the gateway to rank candidates for a role")
    rk.add_argument("role", help="registry @match role, e.g. sk-tools")
    rk.set_defaults(fn=cmd_rank)

    sg = sub.add_parser("suggest", help="build a require= spec and ask the gateway to rank it")
    sg.add_argument("--need", action="append",
                     help="capability required, e.g. tools, vision, sovereign "
                          "(repeatable or comma-separated)")
    sg.add_argument("--ctx", help="minimum context window, e.g. 64k or 131072")
    sg.add_argument("--tier", help="sovereignty ladder, e.g. local,free-remote")
    sg.set_defaults(fn=cmd_suggest)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
