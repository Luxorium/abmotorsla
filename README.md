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
| `content/freight.json` | `STORE_SHIPPING_POLICY_FILE` | **how every part ships.** The groups, their rates, which parts fall in each, the `ship:*` tag CoreYard puts on the product, and who closes an invoiced order out |
| `content/catalog-profile.json` | `STORE_PROFILE_FILE` | what a listing may claim (condition wording, "Genuine OEM", whether OEM belongs in a title), part-type wording, the `abm` metafield namespace, audit thresholds |
| `content/weights.json` | `STORE_WEIGHT_RULES_FILE` | packed shipping weight per part type, applied on every publish |
| `content/order-sync.json` | `STORE_ORDER_POLICY_FILE` | what to tag and note on an invoiced order. Its fulfillment rules are *derived* from `freight.json` |

Put these in the backend's `.env` (paths are absolute):

```
STORE_SHIPPING_POLICY_FILE=/srv/abmotorsla.com/abmotorsla/content/freight.json
STORE_PROFILE_FILE=/srv/abmotorsla.com/abmotorsla/content/catalog-profile.json
STORE_WEIGHT_RULES_FILE=/srv/abmotorsla.com/abmotorsla/content/weights.json
STORE_ORDER_POLICY_FILE=/srv/abmotorsla.com/abmotorsla/content/order-sync.json
STORE_REQUIRE_IMAGES=true
STORE_PUBLICATIONS=Online Store
STORE_CHARM_PRICES=true
```

Check them at any time — offline, no credentials:

```bash
python3 scripts/check_contracts.py        # this repo agrees with itself and with the theme
cd ../coreyard && bin/coreyard validate   # the files agree with the backend's schemas
```

Both run on every pull request (`.github/workflows/contracts.yml`).

## What the theme reads

CoreYard publishes the structured half of a listing as metafields in the `abm` namespace, so
the theme renders named fields instead of taking generated prose apart:

| Metafield | Used by |
|---|---|
| `abm.fitment` | the "Fits these vehicles" table, the related-parts vehicle, `build_vehicles.py` |
| `abm.grade` | the spec table and the card's grade chip |
| `abm.mileage` | the spec table |
| `abm.condition` | the spec table — this is why no template hardcodes a condition line |

Each `abm.fitment` row is `{years, make, model, note, label}`. `label` is exactly the vehicle
tag CoreYard also writes, which is how a product page links to "more parts that fit this
car" without guessing a tag from the title. Tags still do the *filtering* — Shopify cannot
facet a collection on a metafield — but they are no longer how anything is discovered.

The theme falls back gracefully everywhere: a product with no metafields renders without the
table, so nothing breaks while a republish is still in flight.

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
| `build_vehicles.py` | rebuilds `theme/assets/vehicles.json` from `abm.fitment` for the picker |
| `check_contracts.py` | read-only: config, theme and generated assets still agree |
| `make_light_logo.py` | derives a dark-background logo from the existing PNG |
| `finish_listings.sh` | puts newly published variants into the right delivery profile |

`tag_shipping.py` is gone: CoreYard reads `content/freight.json` itself and applies the
`ship:*` tag during the publish that creates the product, so a part is never live wearing no
classification. What `finish_listings.sh` still does is Shopify's own doing — a delivery
profile is a set of variants, and a new variant lands in the default profile until something
moves it, so keep it on a schedule.

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
| `tag_shipping.py` | `STORE_SHIPPING_POLICY_FILE`; applied during publish, `coreyard repair tags` for the backlog |
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

`content/freight.json` is the one file that decides all of it. The delivery profiles enforce
it at checkout, CoreYard puts the `ship:*` tag on the product, and the theme reads that tag
to warn on the product page, in the cart and in the cart drawer. Change `freight.json`, then:

* re-run `setup_shipping.py --apply` so checkout charges the new rate;
* let the next sync republish the affected parts with their new tag (`bin/coreyard --sink
  api --dry-run` first — a tag change moves the fingerprint), or
  `bin/coreyard repair tags --apply` to correct them in place;
* run `scripts/check_contracts.py`, which fails if the theme's copy of the rates or tags no
  longer matches.

The order sync no longer needs updating: it derives which groups ShipStation owns from the
`fulfillment` key on each group here.

Only two Liquid files know a `ship:*` tag exists — `snippets/shipping-class.liquid` maps tag
to group, `snippets/shipping-group.liquid` holds the wording and rates. Everything else
renders one of them. Before, six files each carried their own copy of the same if/elsif
chain and four of them quoted a rate.

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
