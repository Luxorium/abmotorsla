# Claude Code Guidance

Read and follow `AGENTS.md` for the repository’s contributor conventions. This file highlights the operational rules most important when making changes with Claude Code.

## Repository Context

This is a build-free Shopify Online Store 2.0 theme. The deployable storefront is in `theme/`; store copy and catalog classification data are in `content/`; Shopify administration and catalog jobs are in `scripts/`. The static mockup lives in `preview/`, and logos are stored in `brand/`.

Use plain Liquid, CSS, and vanilla JavaScript for storefront work. Do not introduce a framework or package manager without an explicit requirement. Follow existing indentation and naming patterns, preserve accessibility attributes, and keep Shopify section schemas compatible with existing settings.

## Validation

Run checks appropriate to the changed files:

```bash
cd theme && shopify theme check         # theme linting and structural checks
python3 preview/build.py                 # rebuild the self-contained static preview
python3 scripts/audit_listings.py        # read-only catalog quality audit
```

For UI changes, manually test affected product, collection, search, and cart flows with `shopify theme dev -e staging`. Note that `-e live` is **not** a sandbox: that environment pins the published theme with `allow-live`, so the CLI syncs every local edit straight to the storefront. There is no automated test suite.

## Shopify Safety

Treat live-store mutations and publishing as deliberate operations. Use a script’s documented `--plan` or `--dry-run` mode before `--apply`; never infer permission to publish the live theme. Scripts load `SHOPIFY_STORE` and `SHOPIFY_ADMIN_TOKEN` from the sibling CoreYard `.env`, or from the file named by `ABM_ENV`. Never print or commit credentials.

Preserve script idempotency and resume behavior. Do not commit local state files, `.env`, PEM files, or `preview/dist/`. Regenerate `theme/assets/vehicles.json` with `scripts/build_vehicles.py` after major catalog syncs rather than hand-editing it.

## Change Hygiene

Keep changes focused and do not overwrite unrelated work in a dirty worktree. Use short imperative commit subjects. In handoff notes, state validation performed and flag customer-visible effects, store-data mutations, required credential scopes, and any remaining manual publish step.
