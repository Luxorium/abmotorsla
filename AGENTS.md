# Repository Guidelines

## Project Structure & Module Organization

This repository is the **storefront**: the Shopify theme, the brand, the copy, and A&B's own
commercial policy. The catalog pipeline — inventory extraction, product rendering, Shopify
synchronization, orders, reconciliation, repair, auditing — is [CoreYard](../coreyard), a
separate, vendor-neutral backend. Do not add generic catalog automation here; it belongs
upstream, where it is tested and where every yard running CoreYard benefits from it.

`theme/` is the deployable Online Store 2.0 theme; Liquid files live in their matching
subdirectories, and shared CSS, JavaScript and the generated vehicle index are in
`theme/assets/`. Store copy and classification data belong in `content/`. `scripts/` contains
one-purpose Python jobs for store administration and asset generation, all sharing
`scripts/_shopify.py`. Use `preview/` for the static mockup, `brand/` for logos, and `docs/`
for launch guidance.

Four files in `content/` are read by CoreYard through paths set in *its* `.env` —
`freight.json` (`STORE_SHIPPING_POLICY_FILE`), `catalog-profile.json` (`STORE_PROFILE_FILE`),
`weights.json` (`STORE_WEIGHT_RULES_FILE`) and `order-sync.json` (`STORE_ORDER_POLICY_FILE`).
They are an interface: changing a key name or a shipping-group tag changes backend behaviour.
`freight.json` and `catalog-profile.json` both feed the rendered product, so editing either
makes the next sync republish everything it covers.

`content/freight.json` is the single shipping contract. The delivery profiles, the `ship:*`
tag CoreYard writes, the theme's warnings and the order sync's fulfillment rules all derive
from it. Only `snippets/shipping-class.liquid` and `snippets/shipping-group.liquid` may know
a `ship:*` tag or a rate; `scripts/check_contracts.py` enforces that.

## Build, Test, and Development Commands

The theme uses plain Liquid, CSS, and JavaScript; there is no npm install or compilation step.

```bash
cd theme && shopify theme dev -e staging # hot-reloading preview against an unpublished copy
cd theme && shopify theme check         # lint Liquid, JSON, and theme structure
python3 scripts/check_contracts.py       # config, theme and generated assets still agree
python3 preview/build.py                 # create preview/dist/abmotors-preview.html
python3 -m py_compile scripts/*.py       # syntax check
```

`check_contracts.py` runs on every pull request alongside `coreyard validate`, which checks
the same files against the backend's own schemas — see `.github/workflows/contracts.yml`.

Catalog quality, reconciliation and repair are CoreYard commands, run from that checkout:
`bin/coreyard audit catalog`, `bin/coreyard reconcile`, `bin/coreyard repair titles --dry-run`.

> **`theme dev -e live` is not a sandbox.** The `live` environment in `theme/shopify.theme.toml`
> pins the published theme and sets `allow-live`, so the CLI attaches to it and syncs every
> local edit straight to the storefront as you type — including broken intermediate states.
> Use `-e staging` to develop against an unpublished copy.

Before running a store-changing script, use its documented `--plan` or `--dry-run` option. For
example, run `python3 scripts/tag_shipping.py --plan` before adding `--apply`. Scripts read
Shopify credentials from `.env` in this repository (or `ABM_ENV`), falling back to the sibling
CoreYard `.env` for installations that predate this repository having its own.

## Coding Style & Naming Conventions

Follow existing formatting: four spaces and type hints for Python; two-space indentation in
Liquid, JSON, CSS, and JavaScript. Use `snake_case` for Python functions and files, kebab-case
for Liquid files and handles, and CSS custom properties for design tokens. Keep scripts
idempotent and resumable, and prefer Python's standard library. Preserve accessibility
attributes and Shopify schema settings when changing UI.

New scripts use `scripts/_shopify.py` for auth, retry, throttling and paging rather than
hand-rolling another client. Never `sys.path.insert` into `../coreyard` — the two repositories
talk through Shopify, through the configuration files above, and through CoreYard's CLI. CI
checks the backend's schemas by checking that repository out, which is not a runtime import
and must stay that way.

Prefer structured metafields over parsing CoreYard's display strings. `abm.fitment` carries
named fields; a title or a tag is wording that may improve, and anything that reads it back
apart breaks silently when it does. Tags remain the only way to filter a collection, so that
use stays.

## Testing Guidelines

There is no automated test suite here; the tested code is upstream. Run `shopify theme check`,
validate JSON edits with a parser, compile-check changed Python, rebuild the static preview
when relevant, and manually exercise affected product, collection, search, and cart flows
through `shopify theme dev -e staging`. Use read-only audits or `--plan` runs before applying
catalog changes.

## Commit & Pull Request Guidelines

Recent commits use short, imperative summaries such as `Reconcile every claim on the site with
what checkout actually does`. Keep commits focused and explain operational consequences in the
body. Pull requests should summarize customer-visible and store-data effects, list validation,
link issues, and include screenshots for visual changes. Call out scripts requiring `--apply`,
new credential scopes, live-theme publishing, and any change to a file CoreYard reads.

## Security & Generated Files

Never commit `.env` files, tokens, PEM keys, script state files, or `preview/dist/`. Treat
`theme/assets/vehicles.json` as generated output and regenerate it with
`scripts/build_vehicles.py` after major catalog syncs.
