"""Adapter base + registry. An adapter declares which capability+name it provides."""
from __future__ import annotations


class AdapterError(RuntimeError):
    pass


class Adapter:
    """Subclass and set `capability` + `name`. Capability-specific methods are added per family."""
    capability: str = ""
    name: str = ""


class AdapterRegistry:
    def __init__(self):
        self._reg: dict[tuple[str, str], type[Adapter]] = {}

    def register(self, cls: type[Adapter]) -> type[Adapter]:
        if not getattr(cls, "capability", "") or not getattr(cls, "name", ""):
            raise AdapterError(f"{cls.__name__} must set both `capability` and `name`.")
        self._reg[(cls.capability, cls.name)] = cls
        return cls

    def lookup(self, capability: str, name: str) -> type[Adapter]:
        key = (capability, name)
        if key not in self._reg:
            raise AdapterError(f"No adapter {name!r} registered for capability {capability!r}.")
        return self._reg[key]

    def available_for(self, capability: str) -> list[str]:
        return sorted(n for (c, n) in self._reg if c == capability)
