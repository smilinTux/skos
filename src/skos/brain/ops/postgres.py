"""Compatibility shim: ``skos.brain.ops.postgres`` is now ``skbrain.postgres``.

The module object is aliased, not copied, so monkeypatching a name here
patches the framework module. Removed after one minor release.
"""

import sys

import skbrain.postgres as _impl

sys.modules[__name__] = _impl
