"""Compatibility shim: ``skos.brain.ops.writer`` is now ``skbrain.writer``.

The module object is aliased, not copied, so monkeypatching a name here
patches the framework module. Removed after one minor release.
"""

import sys

import skbrain.writer as _impl

sys.modules[__name__] = _impl
