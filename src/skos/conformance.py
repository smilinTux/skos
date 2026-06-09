"""Generic conformance: an adapter must serve a real capability and be a listed provider."""
from __future__ import annotations

from skos.adapter import Adapter
from skos.capability import Catalog, CapabilityError


class ConformanceError(AssertionError):
    pass


def assert_conforms(adapter_cls: type[Adapter], catalog: Catalog) -> None:
    cap_name = getattr(adapter_cls, "capability", "")
    name = getattr(adapter_cls, "name", "")
    if not cap_name or not name:
        raise ConformanceError(f"{adapter_cls.__name__} missing capability/name.")
    try:
        cap = catalog.get(cap_name)
    except CapabilityError as exc:
        raise ConformanceError(str(exc)) from exc
    valid = {cap.default, *cap.alternates}
    if name not in valid:
        raise ConformanceError(
            f"{adapter_cls.__name__}: {name!r} is not a listed adapter for {cap_name!r} "
            f"(expected one of {sorted(valid)}). Add it to capabilities.yaml first."
        )
