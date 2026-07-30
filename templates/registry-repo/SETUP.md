# Standing up the registry repository (maintainer notes)

This directory is the complete, ready-to-push content of the planned
`prokopto-dev/nparseplus-plugins` repository. It lives inside nparse-plus
until that repo exists.

## The name and the URL are load-bearing

`DEFAULT_REGISTRY_URL` in `src/nparseplus/core/plugins/registry.py` is:

```
https://prokopto-dev.github.io/nparseplus-plugins/index.json
```

Every part of that is pinned by the app, so the repository must be created to
match it exactly:

| URL part | Requirement |
| --- | --- |
| `prokopto-dev.github.io` | Owner account is `prokopto-dev`, and the repo is **public** (project Pages on a private repo needs a paid plan). |
| `/nparseplus-plugins/` | Repo name is exactly `nparseplus-plugins`. |
| `/index.json` | `index.json` sits at the **repository root**, and Pages is served from the root of the publishing branch. |

⚠️ **The registry repo name has no hyphen in "nparseplus".** The app repo is
`nparse-plus`; the registry repo is `nparseplus-plugins`. Creating
`nparse-plus-plugins` produces a URL the app will never fetch, with no error
beyond a permanent "Registry unavailable".

Everything above is satisfiable with a stock "deploy from a branch" Pages
configuration — no layout gymnastics and no build step required.

## Create the repository

```bash
# From an nparse-plus checkout:
mkdir /tmp/nparseplus-plugins && cp -R templates/registry-repo/. /tmp/nparseplus-plugins/
cd /tmp/nparseplus-plugins

git init -b main
git add -A
git commit -m "feat: seed the nParse+ plugin registry"

gh repo create prokopto-dev/nparseplus-plugins --public \
  --source . --push \
  --description "Curated plugin index for nParse+"
```

Keep `SETUP.md` in the published repo (it documents the Pages contract) or
delete it before the first commit — either is fine.

## Enable GitHub Pages

Classic branch-source Pages, branch `main`, folder `/` (root):

```bash
gh api -X POST repos/prokopto-dev/nparseplus-plugins/pages \
  -f "source[branch]=main" \
  -f "source[path]=/"
```

If Pages was already enabled, use `-X PUT` to change the source instead of
`-X POST`. Check where it stands:

```bash
gh api repos/prokopto-dev/nparseplus-plugins/pages \
  --jq '{status, html_url, source}'
```

Wait for `status` to become `built` (usually under a minute; the first build
can take a few).

### Verify the exact URL the app uses

Do not skip this — it is the only step that proves the app can reach it:

```bash
curl -fsSL -o /tmp/live-index.json \
  https://prokopto-dev.github.io/nparseplus-plugins/index.json
diff <(python -m json.tool index.json) <(python -m json.tool /tmp/live-index.json) \
  && echo "OK: the published index matches the committed one"
```

Also confirm the schema is reachable, since the URL is baked into the
schema's `$id`:

```bash
curl -fsSI https://prokopto-dev.github.io/nparseplus-plugins/schema/index-v1.schema.json \
  | head -1
```

### A note on Jekyll

Pages runs Jekyll by default. That is deliberate here: it renders `README.md`
as the site's landing page, and copies `index.json` and `schema/` through
verbatim (Jekyll does not process files without front matter). If a future
Jekyll change ever mangles them, commit an empty `.nojekyll` at the root —
`index.json` will then still serve correctly, at the cost of the rendered
landing page (`/` will 404).

## Protect the branch

The index is only as trustworthy as the merges into it. Require review and a
green validation run:

```bash
gh api -X PUT repos/prokopto-dev/nparseplus-plugins/branches/main/protection \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["validate"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

Also turn off the settings that would let a fork PR do more than propose text:

```bash
# Fork PRs get a read-only token and no secrets by default; make sure
# workflows cannot be approved automatically for first-time contributors.
gh api -X PUT repos/prokopto-dev/nparseplus-plugins/actions/permissions/workflow \
  -f default_workflow_permissions=read \
  -F can_approve_pull_request_reviews=false
```

Note that `validate-index.yml` triggers on `pull_request`, so a PR can modify
the workflow itself. That is expected and is why review, not CI, is the trust
boundary — the job prints a reviewer notice whenever a PR touches anything
other than `index.json` and `owners.json`.

## Keeping the schema in sync

`schema/index-v1.schema.json` is generated, in the **app** repo, from the
pydantic models that the client actually parses with:

```bash
# in an nparse-plus checkout
uv run python tools/gen_registry_schema.py
uv run python tools/gen_registry_schema.py --check   # what CI asserts
```

When `nparseplus.core.plugins.registry` changes, regenerate there and copy the
result into this repository. Never hand-edit it here: the whole point is that
the registry's CI and the app cannot disagree about what a valid entry is.

A `schema_version` bump is a breaking change for every already-released
nParse+ (older clients refuse the index and tell the user to update), so it
needs a deprecation plan, not just a regeneration.

## Afterwards

- Update the "Status" admonition in `docs/plugins/registry.md` in the app repo
  — it currently says the registry is not live yet.
- Point `CONTRIBUTING.md`'s template link at the real
  `prokopto-dev/nparseplus-plugin-template` repo once that exists (see
  `templates/plugin-repo/TEMPLATE_SETUP.md`).
- Delete `templates/registry-repo/` from the app repo once this repository is
  the source of truth, along with the parts of
  `tests/core/plugins/test_registry_schema.py` that read it — but keep
  `tools/gen_registry_schema.py`, which is what regenerates the schema.
