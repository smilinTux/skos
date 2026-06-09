"""The declarative app contract. References capabilities, not hardcoded infra."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError, model_validator


class DescriptorError(ValueError):
    pass


class OciSpec(BaseModel):
    image: str
    ports: list[int] = []
    env: dict[str, str] = {}


class Packaging(BaseModel):
    oci: OciSpec | None = None
    native: dict | None = None  # adapter added in a later sub-project

    @model_validator(mode="after")
    def at_least_one(self):
        if not self.oci and not self.native:
            raise ValueError("packaging must declare at least one adapter (oci|native)")
        return self


class AppDescriptor(BaseModel):
    name: str
    capability: str
    description: str = ""
    packaging: Packaging
    data: list[str] = []


def load_descriptor(path: str | Path) -> AppDescriptor:
    try:
        raw = yaml.safe_load(Path(path).read_text())
        return AppDescriptor.model_validate(raw)
    except (ValidationError, yaml.YAMLError) as exc:
        raise DescriptorError(f"Invalid app.yaml at {path}: {exc}") from exc
