"""Plugin host — discovery, loading, activation, and lifecycle of plugins.

Qt-free (enforced by tests/test_architecture.py). The public plugin contract
lives in the separate ``nparseplus-sdk`` package; this package is the host
side that implements it: ``PluginHost`` owns the lifecycle,
``HostPluginContext`` is the capability object handed to ``activate``, and
``install`` is the engine behind the in-app installer.
"""
