# abmotorsla.com — A&B Motors storefront

The Shopify theme for **abmotorsla.com** (store `5faf0h-z1.myshopify.com`), built to sell the
salvage inventory that [CoreYard](../coreyard) publishes from the yard system.

Plain **Liquid + CSS + JavaScript**. No framework, no build step, no npm install.

```
theme/            the Shopify theme — this is what gets pushed to the store
  assets/         base.css (whole design system) + theme.js (all behaviour) + vehicles.json
  layout/         theme.liquid, password.liquid
  sections/       header, footer, hero, product, collection, search, cart, shop-by-vehicle, …
  snippets/       card-product, price, search-field, icon, shipping-group, credibility, …
  templates/      index.json, product.json, collection.json, … (Online Store 2.0)
  config/         settings_schema.json (theme editor), settings_data.json (our defaults)
content/          store content, applied by the scripts — pages, policies, and the
                  classification tables (site.json, freight.json, weights.json)
scripts/          one job each, all idempotent and resumable (see below)
preview/          static HTML mockup — open in a browser, no Shopify needed
brand/            logo assets, including the generated dark-background variant
docs/             launch runbook
```

## Scripts

Every one is idempotent, has a `--plan` or `--dry-run`, and resumes from a state file if
interrupted. They read credentials from the CoreYard `.env` (override with `ABM_ENV`).

| Script | What it does |
|---|---|
| `store_setup.py` | collections, pages, policies, menus, and the draft→active flip |
| `setup_shipping.py` | delivery profiles: free ground, $299.99 freight, $199.99 freight, pickup-only |
| `tag_shipping.py` | writes `ship:*` tags so the theme can warn before checkout |
| `backfill_weights.py` | packed shipping weight per part type, from `content/weights.json` |
| `charm_prices.py` | rounds prices to the nearest .99 (one-off; the rule now lives in CoreYard) |
| `cleanup_listings.py` | deletes sample products, strips "– OEM" from titles, fills photo alt text |
| `audit_listings.py` | read-only quality report: missing SKU, thin description, no weight, … |
| `build_vehicles.py` | rebuilds `theme/assets/vehicles.json` for the year/make/model picker |
| `poll_orders.py` | fetches new orders and renders CoreYard pull tickets (needs `read_orders`) |
| `make_light_logo.py` | derives a dark-background logo from the existing PNG |

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
`setup_shipping.py --apply` and `tag_shipping.py --apply` to keep the two in step.

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

The static mockup in `preview/` needs no Shopify at all — open `preview/index.html` in a browser
to review layout and color without touching the store.

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

- Products are pushed by CoreYard with handle `abm-<uid>`, SKU = the yard's stock number,
  long SEO titles, `productType` = part type, and year/make/model tags.
- Collection filters come from Shopify's own **Search & Discovery** filters — turn tags into
  filters in admin (Online Store → Navigation → Filters) and the sidebar fills in automatically.
- The product page reads optional metafields under the `abm` namespace: `grade`, `mileage`, and
  `fitment` (a list of `{years, make, model, note}`) for the "Fits these vehicles" table. Without
  them the page still renders — the fitment list also lives in the product description CoreYard
  writes.
