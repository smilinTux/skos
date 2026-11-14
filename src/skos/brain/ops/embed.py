"""Compatibility shim: ``skos.brain.ops.embed`` is now ``skbrain.embed``.

The module object is aliased, not copied, so monkeypatching a name here
patches the framework module. Removed after one minor release.
"""

import sys

import skbrain.embed as _impl

sys.modules[__name__] = _impl
