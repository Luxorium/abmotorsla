# Catalogue audit — 2026-08-20

Baseline taken before the first Merchant Center sync, so feed rejections can be told
apart from problems that were already there. Read-only: `scripts/audit_listings.py`
plus targeted follow-up queries. Nothing was changed.

**Catalogue:** 10,221 products — 9,326 ACTIVE, 761 DRAFT, 134 ARCHIVED.
Only ACTIVE products reach the storefront and the feed, so every count below is
re-checked against ACTIVE alone. That distinction matters: the raw audit numbers look
alarming and mostly are not.

## What the raw audit reported, and what it actually means

| Finding | All products | ACTIVE only | Verdict |
|---|---:|---:|---|
| No photos | 762 | **0** | Working as designed |
| Photo missing alt text | 762 | **0** | Working as designed |
| No shipping weight | 818 | **56** | Worth fixing |
| Suspicious title | 64 | 64 | Worth a look |
| Missing SKU / thin description / zero price / duplicate SKU | 0 | 0 | Clean |

### Photos — not a problem

Every one of the 762 is a DRAFT or ARCHIVED product. **All 9,326 active products have at
least one photo, and every one of those photos has alt text.** That is `reconcile_catalog.py`
doing its job: it only promotes a part to ACTIVE once it has been photographed, because the
storefront promises "you buy the part in the picture" in four places.

This is the single most important thing on this page for Merchant Center, because
`image_link` is a required field and a missing image is an automatic rejection. The feed
carries only ACTIVE products, so there is no exposure here.

### Shipping weight — 56 active products, worth fixing

Small, but not nothing. It includes 7 transmissions, 4 engines and 3 transfer cases.

Flat-rate freight is unaffected — those parts charge $299.99 or $199.99 regardless of
weight. The cost is on ground parts, where the UPS calculated option at checkout cannot
price a part with no weight.

**Fix:** `python3 scripts/backfill_weights.py --plan`, then `--apply` once approved. The
per-type weights already live in `content/weights.json`.

### Suspicious titles — 64 active products

Titles missing the model, e.g. `2007 Cadillac Glove Box`, `2006 Chevrolet Glove Box`.
A shopper cannot tell whether it fits, and the title is the strongest single field in
both organic search and the Shopping feed.

These come from the yard system, so the durable fix is upstream in CoreYard's title
builder rather than a one-off rewrite here — a rewrite would be overwritten on the next
sync. Low priority next to everything else, but worth queueing.

## Nothing found for

Missing SKU, thin or empty description, missing SEO meta, zero or absurd price, zero
inventory while ACTIVE, duplicate SKUs. All clean across the whole catalogue.

---
*Re-run with `python3 scripts/audit_listings.py`. Read-only, safe any time.*
