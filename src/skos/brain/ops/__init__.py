"""Compatibility shim: ``skos.brain.ops`` is now the ``skbrain`` framework package.

The engine moved to the public ``skbrain`` repo (extraction design 2026-11-14).
Every name that used to live here is re-exported from ``skbrain``. This shim is
removed after one minor release; import from ``skbrain`` directly.
"""

from skbrain import *  # noqa: F401,F403
from skbrain import __all__  # noqa: F401
