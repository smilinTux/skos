"""Compatibility shim: ``skos.brain.ops.chunker`` is now ``skbrain.chunker``.

The module object is aliased, not copied, so monkeypatching a name here
patches the framework module. Removed after one minor release.
"""

import sys

import skbrain.chunker as _impl

sys.modules[__name__] = _impl
