# Claude Code Guidance

Read and follow `AGENTS.md` for the repository's contributor conventions. This file
highlights the operational rules most important when making changes with Claude Code.

## Repository Context

This is the **storefront**: a build-free Shopify Online Store 2.0 theme plus A&B Motors'
branding, copy, and commercial policy. The deployable storefront is in `theme/`; store copy
and catalog classification data are in `content/`; Shopify administration jobs are in
`scripts/`. The static mockup lives in `preview/`, and logos are stored in `brand/`.

The catalog pipeline is **not here**. [CoreYard](../coreyard) reads the yard system, renders
every product, publishes to Shopify, reconciles, repairs, audits, and handles orders. Before
adding any catalog automation to this repository, ask whether another salvage yard could use
it unchanged — if so it belongs upstream in CoreYard, where it has tests and a neutrality
check. What belongs here is what is specific to A&B: branding, rates, policies, collection
design, shipping classifications, copy, and merchandising decisions.

Use plain Liquid, CSS, and vanilla JavaScript for storefront work. Do not introduce a
framework or package manager without an explicit requirement. Follow existing indentation and
naming patterns, preserve accessibility attributes, and keep Shopify section schemas
compatible with existing settings.

## The CoreYard boundary

Three files in `content/` are an interface, read by CoreYard through paths configured in
*its* `.env`:

| File | CoreYard setting |
|---|---|
| `content/catalog-profile.json` | `STORE_PROFILE_FILE` |
| `content/weights.json` | `STORE_WEIGHT_RULES_FILE` |
| `content/order-sync.json` | `STORE_ORDER_POLICY_FILE` |

`catalog-profile.json` carries what a listing is allowed to claim. Those strings are part of
the rendered product and the rendered product is what the sync fingerprints, so **editing it
makes the next sync republish every product it covers** — check the count with
`bin/coreyard --sink api --dry-run` before committing a wording change.

`order-sync.json` and `tag_shipping.py` share a vocabulary: the `ship:*` tags. Renaming a
group in `content/freight.json` means updating both, or the order sync silently falls through
to its default group and starts fulfilling parcel orders ShipStation should have closed.

Never `sys.path.insert` into `../coreyard`, and never import a CoreYard module. The two
repositories talk through Shopify, through those configuration files, and through CoreYard's
CLI. Scripts here share `scripts/_shopify.py` for auth, retry, throttling and paging.

## Validation

Run checks appropriate to the changed files:

```bash
cd theme && shopify theme check          # theme linting and structural checks
python3 preview/build.py                  # rebuild the self-contained static preview
python3 -m py_compile scripts/*.py        # syntax check
python3 -c 'import json,glob; [json.load(open(f)) for f in glob.glob("content/*.json")]'
```

Catalog quality is a CoreYard command: `bin/coreyard audit catalog` from that checkout.

For UI changes, manually test affected product, collection, search, and cart flows with
`shopify theme dev -e staging`. Note that `-e live` is **not** a sandbox: that environment
pins the published theme with `allow-live`, so the CLI syncs every local edit straight to the
storefront. There is no automated test suite here.

## Shopify Safety

Treat live-store mutations and publishing as deliberate operations. Use a script's documented
`--plan` or `--dry-run` mode before `--apply`; never infer permission to publish the live
theme. Scripts load `SHOPIFY_STORE` and `SHOPIFY_ADMIN_TOKEN` from `.env` in this repository,
from the file named by `ABM_ENV`, or — for installations set up before this repository had its
own — from the sibling CoreYard `.env`. Never print or commit credentials.

Preserve script idempotency and resume behavior. Do not commit local state files, `.env`, PEM
files, or `preview/dist/`. Regenerate `theme/assets/vehicles.json` with
`scripts/build_vehicles.py` after major catalog syncs rather than hand-editing it.

## Change Hygiene

Keep changes focused and do not overwrite unrelated work in a dirty worktree. Use short
imperative commit subjects. In handoff notes, state validation performed and flag
customer-visible effects, store-data mutations, required credential scopes, changes to any
file CoreYard reads, and any remaining manual publish step.
