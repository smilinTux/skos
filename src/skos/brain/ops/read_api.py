"""Compatibility shim: ``skos.brain.ops.read_api`` is now ``skbrain.read_api``.

The module object is aliased, not copied, so monkeypatching a name here
patches the framework module. Removed after one minor release.
"""

import sys

import skbrain.read_api as _impl

sys.modules[__name__] = _impl
