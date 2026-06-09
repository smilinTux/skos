"""PackagingAdapter — the port every packaging adapter conforms to."""
from __future__ import annotations

import abc
from dataclasses import dataclass

from skos.descriptor import AppDescriptor


@dataclass
class InstallResult:
    name: str
    adapter: str
    ref: str        # image ref / install path
    running: bool


class PackagingAdapter(abc.ABC):
    name: str

    @abc.abstractmethod
    def materialize(self, app: AppDescriptor) -> InstallResult: ...

    @abc.abstractmethod
    def locate(self, app: AppDescriptor) -> str | None: ...

    @abc.abstractmethod
    def health(self, app: AppDescriptor) -> bool: ...

    @abc.abstractmethod
    def remove(self, app: AppDescriptor) -> None: ...
