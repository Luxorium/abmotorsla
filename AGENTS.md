# Repository Guidelines

## Project Structure & Module Organization

`theme/` is the deployable Shopify Online Store 2.0 theme. Liquid files live in their matching subdirectories; shared CSS, JavaScript, and the generated vehicle index are in `theme/assets/`. Store copy and classification data belong in `content/`. `scripts/` contains one-purpose Python jobs for Shopify setup, catalog cleanup, auditing, and asset generation. Use `preview/` for the static mockup, `brand/` for logos, and `docs/` for launch guidance.

## Build, Test, and Development Commands

The theme uses plain Liquid, CSS, and JavaScript; there is no npm install or compilation step.

```bash
cd theme && shopify theme dev -e staging # hot-reloading preview against an unpublished copy
cd theme && shopify theme check         # lint Liquid, JSON, and theme structure
python3 preview/build.py                 # create preview/dist/abmotors-preview.html
python3 scripts/audit_listings.py        # read-only catalog quality report
```

> **`theme dev -e live` is not a sandbox.** The `live` environment in `theme/shopify.theme.toml`
> pins the published theme and sets `allow-live`, so the CLI attaches to it and syncs every
> local edit straight to the storefront as you type — including broken intermediate states.
> Use `-e staging` to develop against an unpublished copy.

Before running a store-changing script, use its documented `--plan` or `--dry-run` option. For example, run `python3 scripts/tag_shipping.py --plan` before adding `--apply`. Scripts read Shopify credentials from the sibling CoreYard `.env`; set `ABM_ENV=/path/to/.env` to override it.

## Coding Style & Naming Conventions

Follow existing formatting: four spaces and type hints for Python; two-space indentation in Liquid, JSON, CSS, and JavaScript. Use `snake_case` for Python functions and files, kebab-case for Liquid files and handles, and CSS custom properties for design tokens. Keep scripts idempotent and resumable, and prefer Python's standard library. Preserve accessibility attributes and Shopify schema settings when changing UI.

## Testing Guidelines

There is no automated test suite or coverage threshold. Run `shopify theme check`, rebuild the static preview when relevant, and manually exercise affected product, collection, search, and cart flows through `shopify theme dev`. Validate JSON edits with a parser and use read-only audits or dry runs before applying catalog changes.

## Commit & Pull Request Guidelines

Recent commits use short, imperative summaries such as `Reconcile every claim on the site with what checkout actually does`. Keep commits focused and explain operational consequences in the body. Pull requests should summarize customer-visible and store-data effects, list validation, link issues, and include screenshots for visual changes. Call out scripts requiring `--apply`, new credential scopes, or live-theme publishing.

## Security & Generated Files

Never commit `.env` files, tokens, PEM keys, script state files, or `preview/dist/`. Treat `theme/assets/vehicles.json` as generated output and regenerate it with `scripts/build_vehicles.py` after major catalog syncs.
