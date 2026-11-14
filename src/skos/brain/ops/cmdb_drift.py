"""Compatibility shim: ``skos.brain.ops.cmdb_drift`` is now ``skbrain.cmdb_drift``.

The module object is aliased, not copied, so monkeypatching a name here
patches the framework module. Removed after one minor release.
"""

import sys

import skbrain.cmdb_drift as _impl

sys.modules[__name__] = _impl
