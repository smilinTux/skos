"""Compatibility shim: ``skos.brain.ops.cli`` is now ``skbrain.cli``.

The module object is aliased, not copied, so monkeypatching a name here
patches the framework module. Removed after one minor release.
"""

import sys

import skbrain.cli as _impl

sys.modules[__name__] = _impl
