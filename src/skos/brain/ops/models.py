"""Compatibility shim: ``skos.brain.ops.models`` is now ``skbrain.models``.

The module object is aliased, not copied, so monkeypatching a name here
patches the framework module. Removed after one minor release.
"""

import sys

import skbrain.models as _impl

sys.modules[__name__] = _impl
