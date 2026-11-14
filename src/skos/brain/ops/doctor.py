"""Compatibility shim: ``skos.brain.ops.doctor`` is now ``skbrain.doctor``.

The module object is aliased, not copied, so monkeypatching a name here
patches the framework module. Removed after one minor release.
"""

import sys

import skbrain.doctor as _impl

sys.modules[__name__] = _impl
