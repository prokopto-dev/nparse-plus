# nparseplus-sdk

The stable contract for building [nParse+](https://github.com/prokopto-dev/nparse-plus)
plugins: addon windows, log parsers, event subscribers, and network pollers
that load into the app at runtime.

- **Plugin authors:** `pip install nparseplus-sdk`, subclass
  `nparseplus_sdk.NParsePlugin`, expose a module-level `create_plugin()`
  factory, and check your work with `nparseplus-plugin validate <path>`.
  Full guide: the *Plugins* section of the
  [nParse+ documentation](https://prokopto-dev.github.io/nparse-plus/).
- **Versioning:** this package is versioned independently of the app.
  Declare the SDK range you built against in
  `PluginMeta.requires_sdk` (e.g. `">=1.0,<2"`); the app refuses plugins
  whose range does not admit the SDK version it bundles.
- **No dependency on the app:** plugins execute inside nParse+, which
  provides the runtime. `nparseplus_sdk.events` and `nparseplus_sdk.ui`
  re-export host classes lazily; for standalone type-checking/tests install
  the app from source
  (`pip install git+https://github.com/prokopto-dev/nparse-plus`).

## Repository note

This package currently lives in the `sdk/` directory of the main
`prokopto-dev/nparse-plus` repository as an independent uv workspace member.
It is slated to move to its own repository (`prokopto-dev/nparseplus-sdk`)
and to be published to PyPI; until then, install it from a checkout:

```bash
pip install ./sdk        # from a nparse-plus checkout
```

## Publishing checklist (for maintainers, once the repo split happens)

1. `git subtree split -P sdk` into `prokopto-dev/nparseplus-sdk`.
2. Set up trusted publishing (PyPI) + a release workflow.
3. Switch the app's dependency from the workspace source to the PyPI range
   in the root `pyproject.toml`.
