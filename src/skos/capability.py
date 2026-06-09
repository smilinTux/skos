"""Capability catalog — the sk* ports, their 4C group, and recommended adapters."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel


class CapabilityError(KeyError):
    pass


class Capability(BaseModel):
    name: str
    group: str           # cloud | comms | compute | core
    description: str = ""
    default: str         # sovereign default adapter
    alternates: list[str] = []
    profiles: dict[str, str] = {}   # profile -> adapter override


class Catalog:
    def __init__(self, capabilities: list[Capability]):
        self._by_name = {c.name: c for c in capabilities}

    @classmethod
    @lru_cache(maxsize=1)
    def load(cls) -> "Catalog":
        # Try importlib.resources first, fall back to path-relative
        try:
            from importlib.resources import files
            raw = yaml.safe_load(files("skos").joinpath("capabilities.yaml").read_text())
        except Exception:
            yaml_path = Path(__file__).parent / "capabilities.yaml"
            raw = yaml.safe_load(yaml_path.read_text())
        return cls([Capability.model_validate(c) for c in raw["capabilities"]])

    def all(self) -> list[Capability]:
        return list(self._by_name.values())

    def get(self, name: str) -> Capability:
        if name not in self._by_name:
            raise CapabilityError(f"Unknown capability {name!r}.")
        return self._by_name[name]

    def by_group(self, group: str) -> list[Capability]:
        return [c for c in self._by_name.values() if c.group == group]
