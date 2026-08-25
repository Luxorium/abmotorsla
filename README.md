# abmotorsla.com — A&B Motors storefront

This repository is the **A&B Motors Shopify storefront and site configuration**:
the theme, the brand, the copy, and the commercial policy that makes the store ours.
[CoreYard](../coreyard) is the backend that publishes and synchronizes the inventory —
it reads the yard system, renders the products, and keeps Shopify in step.

The boundary in one line: **if another salvage yard could run it unchanged, it belongs in
CoreYard. If it carries our branding, rates, policies, phone number, collections, copy, or
merchandising decisions, it belongs here.**

Plain **Liquid + CSS + JavaScript**. No framework, no build step, no npm install.

```
theme/            the Shopify theme — this is what gets pushed to the store
  assets/         base.css (whole design system) + theme.js (all behaviour) + vehicles.json
  layout/         theme.liquid, password.liquid
  sections/       header, footer, hero, product, collection, search, cart, shop-by-vehicle, …
  snippets/       card-product, price, search-field, icon, shipping-group, credibility, …
  templates/      index.json, product.json, collection.json, … (Online Store 2.0)
  config/         settings_schema.json (theme editor), settings_data.json (our defaults)
content/          store content and the classification tables — pages, policies, and the
                  JSON files CoreYard reads (see "What CoreYard reads from here")
scripts/          storefront administration, one job each, all idempotent
preview/          static HTML mockup — open in a browser, no Shopify needed
brand/            logo assets, including the generated dark-background variant
docs/             launch runbook
```

## What CoreYard reads from here

Three files in `content/` are consumed by the backend. CoreYard never imports this
repository and never assumes a sibling checkout — it is given each path in its own `.env`:

| File | CoreYard setting | What it decides |
|---|---|---|
| `content/catalog-profile.json` | `STORE_PROFILE_FILE` | what a listing may claim (condition wording, "Genuine OEM", whether OEM belongs in a title), part-type wording, which tag prefixes we own, audit thresholds |
| `content/weights.json` | `STORE_WEIGHT_RULES_FILE` | packed shipping weight per part type, applied on every publish |
| `content/order-sync.json` | `STORE_ORDER_POLICY_FILE` | how an invoiced order is tagged, and which shipping groups ShipStation owns so they are *not* fulfilled early |

Put these in the backend's `.env` (paths are absolute):

```
STORE_PROFILE_FILE=/srv/abmotorsla.com/abmotorsla/content/catalog-profile.json
STORE_WEIGHT_RULES_FILE=/srv/abmotorsla.com/abmotorsla/content/weights.json
STORE_ORDER_POLICY_FILE=/srv/abmotorsla.com/abmotorsla/content/order-sync.json
STORE_REQUIRE_IMAGES=true
STORE_PUBLICATIONS=Online Store
STORE_CHARM_PRICES=true
```

> **Editing `catalog-profile.json` republishes the catalog.** Those strings are part of the
> rendered product and the rendered product is what the sync fingerprints, so an edit makes
> the next run rewrite every product it covers. Check the count first with
> `bin/coreyard --sink api --dry-run`.

## Scripts

Every one is idempotent, has a `--plan`/`--apply`, and resumes from a state file if
interrupted. They share one Shopify client (`scripts/_shopify.py`) and read credentials from
`.env` in this repository, or wherever `ABM_ENV` points. None of them import CoreYard.

| Script | What it does |
|---|---|
| `store_setup.py` | collections, pages, policies, menus, and the draft→active flip |
| `setup_shipping.py` | delivery profiles: free ground, $299.99 freight, $199.99 freight, pickup-only |
| `tag_shipping.py` | writes `ship:*` tags so the theme can warn before checkout |
| `build_vehicles.py` | rebuilds `theme/assets/vehicles.json` for the year/make/model picker |
| `make_light_logo.py` | derives a dark-background logo from the existing PNG |
| `finish_listings.sh` | runs the delivery-profile and tagging steps over newly published parts |

### Catalog jobs now live in CoreYard

These used to be scripts here. Each one was generic backend work, and keeping a second copy
next to CoreYard's own pipeline meant two implementations that could disagree about what a
product should say. Run them from the CoreYard checkout:

| Was | Now |
|---|---|
| `reconcile_catalog.py` | `coreyard reconcile [--apply] [--activate]` |
| `retitle_from_coreyard.py` | `coreyard repair titles --dry-run` / `--apply` |
| `backfill_tags.py` | `coreyard repair tags --dry-run` / `--apply` |
| `backfill_weights.py` | published on every sync; `coreyard repair weights --apply` for old products |
| `charm_prices.py` | `STORE_CHARM_PRICES=true`; a normal sync corrects old prices |
| `audit_listings.py` | `coreyard audit catalog` |
| `poll_orders.py` | `coreyard orders poll` |
| `sync_invoiced.py` | `coreyard orders sync-status [--apply]`, with our policy in `content/order-sync.json` |
| `cleanup_listings.py` | `coreyard repair titles` (the "– OEM" suffix is never generated now); `coreyard altfix` for alt text |
| `publish_online.py` | `STORE_PUBLICATIONS=Online Store`; `coreyard reconcile --apply` for the backlog |

`ship:*` tags survive all of it: CoreYard preserves any namespaced tag it did not generate,
so a catalog update can no longer delete the warning the theme depends on.

## How shipping works

Three groups, defined by product type in `content/freight.json`, matching how the yard
already ships on eBay:

| Group | Parts | Rate |
|---|---|---|
| free ground | ~8,500 (90%) | free, plus UPS calculated as a paid option |
| freight — oversize | ~745 | **$299.99** flat, commercial address required |
| freight — heavy | ~60 | **$199.99** flat, commercial address required |
| pickup only | ~100 | **not shipped** — body panels and glass, pickup or local delivery |

The delivery profiles enforce it at checkout; the `ship:*` tags let the theme say so on the
product page, in the cart, and in the cart drawer. Change `freight.json`, then re-run
`setup_shipping.py --apply` and `tag_shipping.py --apply` to keep the two in step — and
`content/order-sync.json` if the group names themselves change, because the order sync reads
the same tags to decide what ShipStation owns.

## Working on it

```bash
# 1. link the local folder to the store (once)
cd theme
shopify theme dev -e staging
#    → serves http://127.0.0.1:9292 rendering REAL products through this theme,
#      hot-reloading as files change. Nothing on the live site is touched.

# 2. when it's ready, upload as an unpublished theme
shopify theme push --unpublished --theme "A&B Motors — Yard"
#    → preview it from Online Store → Themes → Preview, then Publish when happy.

# 3. lint
shopify theme check
```

The static mockup in `preview/` needs no Shopify at all — open `preview/index.html` in a
browser to review layout and color without touching the store.

## Design

Everything comes from the logo: the chrome/steel wordmark and the green recycle mark.

| Token | Value | Where it came from |
|---|---|---|
| `--green` | `#25BA1C` | the recycle mark in the logo; also the old site's link-hover green |
| `--green-dark` | `#13790F` | the deep green used throughout the old WordPress build |
| `--graphite` | `#171C1F` | the near-black outline of the wordmark |
| `--chrome-grad` | silver gradient | sampled from the wordmark's metallic bevel |

Type is Archivo (headings, heavy and industrial) over Assistant (body) — both free Shopify fonts,
so no external font requests.

## Notes for the parts catalog

- Products are pushed by CoreYard with handle `abm-<R#>`, SKU = the yard's R#, long SEO
  titles, `productType` = the expanded part type, and year/make/model tags.
- Collection filters come from Shopify's own **Search & Discovery** filters — turn tags into
  filters in admin (Online Store → Navigation → Filters) and the sidebar fills in automatically.
- The product page reads optional metafields under the `abm` namespace: `grade`, `mileage`, and
  `fitment` (a list of `{years, make, model, note}`) for the "Fits these vehicles" table. Without
  them the page still renders — the fitment list also lives in the product description CoreYard
  writes.
- Regenerate `theme/assets/vehicles.json` with `scripts/build_vehicles.py` after a major sync.
