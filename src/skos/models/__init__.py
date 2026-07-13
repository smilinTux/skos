"""skos.models: single source of truth for LLM/embedding model selection.

One registry file (YAML) defines:
  - backends : concrete servable endpoints (url + model + ctx + kind + vision …)
  - roles    : logical names (sk-default, sk-synth, sk-code, sk-vision, sk-embed)
               mapped to a backend
  - contexts : the TOGGLE switch, a named context (group-chat / job / service /
               agent) overrides the role/backend for that context only
  - defaults : the fallback role (sk-default)

Resolution precedence:  context  >  service  >  role  >  default

Public API:
    from skos.models import resolve, load_registry
    b = resolve(role="sk-vision")          # -> Backend(url, model, ctx, vision, kind)
    b = resolve(context="chat:dr-chiro-group")
    b = resolve(service="skingest.vision")

Keep deps minimal (pyyaml only). Unknown role/context never crashes, it falls
back to the default role with a warning on stderr.
"""
from __future__ import annotations

import os
import sys
import warnings
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception as e:  # pragma: no cover - pyyaml is a declared dep
    raise RuntimeError("skos.models requires pyyaml") from e

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DEFAULT_REGISTRY = Path.home() / ".skcapstone" / "models" / "registry.yaml"


def registry_path(path: str | os.PathLike | None = None) -> Path:
    """Resolve the registry path: explicit arg > $SKMODELS_REGISTRY > default."""
    if path:
        return Path(path).expanduser()
    env = os.environ.get("SKMODELS_REGISTRY")
    if env:
        return Path(env).expanduser()
    return DEFAULT_REGISTRY


# ---------------------------------------------------------------------------
# Backend model
# ---------------------------------------------------------------------------


@dataclass
class Backend:
    """A concrete, servable model endpoint."""

    name: str
    url: str = ""
    model: str = ""
    kind: str = "chat"          # chat | embed
    ctx: int | None = None
    vision: bool = False
    dim: int | None = None      # embedding dimension (embed kind)
    api: str | None = None      # optional API flavour hint (e.g. llama_cpp_openai, ollama)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        extra = d.pop("extra", {}) or {}
        d.update(extra)
        return {k: v for k, v in d.items() if v is not None}

    # dict-style access convenience
    def __getitem__(self, key: str) -> Any:  # pragma: no cover - trivial
        return getattr(self, key)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_KNOWN_BACKEND_FIELDS = {"url", "model", "kind", "ctx", "vision", "dim", "api"}


class Registry:
    def __init__(self, data: dict[str, Any], source: Path | None = None):
        self.source = source
        self.raw: dict[str, Any] = data or {}
        self.backends: dict[str, dict] = dict(self.raw.get("backends") or {})
        self.roles: dict[str, str] = dict(self.raw.get("roles") or {})
        self.contexts: dict[str, str] = dict(self.raw.get("contexts") or {})
        self.defaults: dict[str, Any] = dict(self.raw.get("defaults") or {})

    # -- default role ------------------------------------------------------
    @property
    def default_role(self) -> str:
        return self.defaults.get("role") or "sk-default"

    # -- backend construction ---------------------------------------------
    def make_backend(self, name: str) -> Backend:
        spec = dict(self.backends.get(name) or {})
        known = {k: spec.get(k) for k in _KNOWN_BACKEND_FIELDS if k in spec}
        extra = {k: v for k, v in spec.items() if k not in _KNOWN_BACKEND_FIELDS}
        return Backend(name=name, extra=extra, **known)

    # -- resolution --------------------------------------------------------
    def _target_to_backend_name(self, target: str) -> str | None:
        """A context/role target may be a role name OR a concrete backend name."""
        if target in self.roles:
            return self.roles[target]
        if target in self.backends:
            return target
        return None

    def resolve(
        self,
        role: str | None = None,
        context: str | None = None,
        service: str | None = None,
    ) -> Backend:
        """Resolve to a Backend applying precedence context > service > role > default."""
        candidate: str | None = None
        origin = ""

        # 1. explicit context key (highest)
        if context and context in self.contexts:
            candidate = self.contexts[context]
            origin = f"context:{context}"
        # 2. service -> looked up as "service:<name>"
        elif service and f"service:{service}" in self.contexts:
            key = f"service:{service}"
            candidate = self.contexts[key]
            origin = key
        # 3. explicit role
        elif role:
            candidate = role
            origin = f"role:{role}"
        # 4. default role
        else:
            candidate = self.default_role
            origin = "default"

        backend_name = self._target_to_backend_name(candidate) if candidate else None

        # sk-auto is a GATEWAY-only marker: SKGateway runs a difficulty classifier
        # per request to pick the real role. Python callers can't classify, so they
        # degrade to the concrete default role (sk-default -> ornith). This makes
        # `defaults.role: sk-auto` safe for direct resolver users (skingest, cluster-ask).
        if candidate == "sk-auto" or backend_name == "auto":
            backend_name = self._target_to_backend_name("sk-default")
            origin = f"{origin}->auto-degrade:sk-default"

        if backend_name is None:
            # Unknown role/context/backend: warn, fall back to default role.
            warnings.warn(
                f"skos.models: unresolved target {candidate!r} (from {origin}); "
                f"falling back to default role {self.default_role!r}",
                stacklevel=2,
            )
            print(
                f"[skos.models] warning: unresolved {candidate!r} (from {origin}); "
                f"falling back to default role {self.default_role!r}",
                file=sys.stderr,
            )
            backend_name = self._target_to_backend_name(self.default_role)

        if backend_name is None or backend_name not in self.backends:
            # Registry is broken enough that even the default role has no backend.
            print(
                f"[skos.models] warning: default role {self.default_role!r} maps to "
                f"no backend; returning an empty backend",
                file=sys.stderr,
            )
            return Backend(name=backend_name or "unknown")

        return self.make_backend(backend_name)


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_CACHE: dict[str, Registry] = {}


def load_registry(path: str | os.PathLike | None = None, *, cache: bool = True) -> Registry:
    """Load the registry from `path` (or env / default). Missing file -> empty registry."""
    p = registry_path(path)
    key = str(p)
    if cache and key in _CACHE:
        return _CACHE[key]
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    else:
        print(
            f"[skos.models] warning: registry not found at {p}; using empty registry",
            file=sys.stderr,
        )
        data = {}
    reg = Registry(data, source=p)
    if cache:
        _CACHE[key] = reg
    return reg


def _invalidate() -> None:
    _CACHE.clear()


def resolve(
    role: str | None = None,
    context: str | None = None,
    service: str | None = None,
    *,
    path: str | os.PathLike | None = None,
) -> Backend:
    """Resolve a Backend. See Registry.resolve. Never raises on bad role/context."""
    return load_registry(path).resolve(role=role, context=context, service=service)


def list_roles(path: str | os.PathLike | None = None) -> dict[str, str]:
    return dict(load_registry(path).roles)


def list_backends(path: str | os.PathLike | None = None) -> dict[str, dict]:
    return dict(load_registry(path).backends)


def list_contexts(path: str | os.PathLike | None = None) -> dict[str, str]:
    return dict(load_registry(path).contexts)


def _rt_yaml():
    """Round-trip YAML handler (ruamel) that PRESERVES comments; None if unavailable."""
    try:
        from ruamel.yaml import YAML
        y = YAML()  # round-trip mode
        y.preserve_quotes = True
        y.indent(mapping=2, sequence=4, offset=2)
        return y
    except Exception:
        return None


def _write_contexts(mutate, *, path: str | os.PathLike | None) -> Path:
    """Load the registry, apply mutate(contexts_dict), write it back preserving
    comments (ruamel round-trip; plain-dump fallback only if ruamel is absent)."""
    p = registry_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    rt = _rt_yaml()
    if rt is not None and p.exists():
        with p.open("r", encoding="utf-8") as fh:
            data = rt.load(fh) or {}
        if data.get("contexts") is None:
            data["contexts"] = {}
        mutate(data["contexts"])
        with p.open("w", encoding="utf-8") as fh:
            rt.dump(data, fh)
    else:  # no ruamel, or brand-new file → plain dump (comments would not exist yet)
        data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
        data = data or {}
        if data.get("contexts") is None:
            data["contexts"] = {}
        mutate(data["contexts"])
        p.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False), encoding="utf-8")
    _invalidate()
    return p


def set_context(
    key: str,
    target: str,
    *,
    path: str | os.PathLike | None = None,
) -> Path:
    """Set contexts[key] = target and write the registry back, PRESERVING comments
    (round-trip via ruamel.yaml). This is what the Telegram/skchat `/model` toggle
    calls, so it must NOT destroy the registry's self-documentation."""
    return _write_contexts(lambda ctx: ctx.__setitem__(key, target), path=path)


def unset_context(
    key: str,
    *,
    path: str | os.PathLike | None = None,
) -> bool:
    """Remove contexts[key] (revert a toggle back to the role/default). Returns
    True if it existed. Preserves comments."""
    existed = key in (list_contexts(path) or {})
    if existed:
        _write_contexts(lambda ctx: ctx.pop(key, None), path=path)
    return existed


__all__ = [
    "Backend",
    "Registry",
    "load_registry",
    "registry_path",
    "resolve",
    "list_roles",
    "list_backends",
    "list_contexts",
    "set_context",
    "unset_context",
    "DEFAULT_REGISTRY",
]
