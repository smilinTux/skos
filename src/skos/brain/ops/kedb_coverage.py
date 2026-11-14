"""Compatibility shim: ``skos.brain.ops.kedb_coverage`` is now ``skbrain.kedb_coverage``.

The module object is aliased, not copied, so monkeypatching a name here
patches the framework module. Removed after one minor release.
"""

import sys

import skbrain.kedb_coverage as _impl

sys.modules[__name__] = _impl
