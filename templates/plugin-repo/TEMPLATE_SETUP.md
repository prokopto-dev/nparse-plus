# Creating the GitHub template repository (maintainer notes)

This directory is the complete content of the planned
`prokopto-dev/nparseplus-plugin-template` repository. It lives inside
nparse-plus until that repo exists. To create it:

```bash
# From a nparse-plus checkout:
mkdir /tmp/plugin-template && cp -R templates/plugin-repo/. /tmp/plugin-template/
cd /tmp/plugin-template
rm TEMPLATE_SETUP.md          # this file stays behind — it's about the split
git init -b main && git add -A && git commit -m "feat: nParse+ plugin template"
gh repo create prokopto-dev/nparseplus-plugin-template --public \
  --source . --push \
  --description "Template for building nParse+ plugins"
# Mark it as a template so 'Use this template' appears:
gh api -X PATCH repos/prokopto-dev/nparseplus-plugin-template -f is_template=true
```

Afterwards:

- Link it from docs/plugins/developing.md ("Starting from the repo
  template") in place of the in-repo path.
- **When `nparseplus-sdk` publishes to PyPI**, replace the git-subdirectory
  install with the plain requirement `nparseplus-sdk>=1.0,<2` in all three
  places that carry it today — each is marked with a `NOTE:` comment:
  `pyproject.toml` (`[project] dependencies`),
  `.github/workflows/ci.yml` (*Install SDK + test deps*), and
  `.github/workflows/release.yml` (*Install SDK*). The optional
  `[project.optional-dependencies] dev` extra also pulls the app itself from
  git; that one stays a git install (nParse+ is not a PyPI package).
- The git installs pin `@master`. Once the app cuts a release containing
  `sdk/`, prefer pinning a tag (`@vX.Y.Z`) so template CI cannot break on an
  unrelated master push.
- The template must keep working with the SDK alone — `ci.yml` deliberately
  does not install nParse+, and
  `tests/core/plugins/test_template.py::test_template_unit_tests_pass_with_only_the_sdk_installed`
  in this repo runs the template's suite with `nparseplus` unimportable to
  prove it. Keep host-dependent work after the optional-import guard in
  `activate()`.
- Keep this in-repo copy as the source of truth until the split, then
  delete `templates/plugin-repo/` here (the template repo takes over);
  `tests/core/plugins/test_template.py` guards the copy while it lives
  here — remove it in the same change.
