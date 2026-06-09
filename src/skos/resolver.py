"""Resolver — the heart of framework-guided bloom. Precedence: override > profile > default."""
from __future__ import annotations

from skos.capability import Catalog, CapabilityError


class ResolveError(RuntimeError):
    pass


def resolve(capability: str, *, profile: str, override: str | None = None) -> str:
    try:
        cap = Catalog.load().get(capability)
    except CapabilityError as exc:
        raise ResolveError(str(exc)) from exc

    if override is not None:
        if override != cap.default and override not in cap.alternates:
            raise ResolveError(
                f"Adapter {override!r} is not valid for {capability!r}; "
                f"choose from {[cap.default, *cap.alternates]}."
            )
        return override
    return cap.profiles.get(profile, cap.default)
