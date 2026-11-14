"""Compatibility shim: ``skos.brain.ops.secrets`` is now ``skbrain.secrets``.

The module object is aliased, not copied, so monkeypatching a name here
patches the framework module. Removed after one minor release.
"""

import sys

import skbrain.secrets as _impl

sys.modules[__name__] = _impl
