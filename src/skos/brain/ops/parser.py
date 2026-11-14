"""Compatibility shim: ``skos.brain.ops.parser`` is now ``skbrain.parser``.

The module object is aliased, not copied, so monkeypatching a name here
patches the framework module. Removed after one minor release.
"""

import sys

import skbrain.parser as _impl

sys.modules[__name__] = _impl
