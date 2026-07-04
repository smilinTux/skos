"""skmodels — CLI for the skos model registry (single source of truth).

Subcommands:
  list                          roles + backends + contexts + default
  get <role|context>            what a role or context key resolves to
  resolve [--role R] [--context C] [--service S]
                                print url + model (precedence context>service>role>default)
  set <context-key> <role|backend>
                                the TOGGLE (e.g. skmodels set chat:dr-chiro-group sk-vision)
  test <role>                   curl the backend and report up/down

Registry path: $SKMODELS_REGISTRY or ~/.skcapstone/models/registry.yaml
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from urllib.error import URLError, HTTPError

from skos.models import (
    Backend,
    load_registry,
    registry_path,
    resolve,
    set_context,
    unset_context,
)


def _p(*a):
    print(*a)


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
        _p("  (none set — use `skmodels set <key> <role|backend>`)")
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
        # embed endpoints (e.g. Ollama /api/embed) — probe the /api/tags sibling
        base = b.url.rsplit("/api/", 1)[0] if "/api/" in b.url else b.url.rstrip("/")
        probe = base.rstrip("/") + "/api/tags"
    else:
        probe = b.url.rstrip("/") + "/models"
    try:
        req = urllib.request.Request(probe, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(4096).decode("utf-8", "replace")
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
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
