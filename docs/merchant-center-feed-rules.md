# Merchant Center feed rules — shipping groups

Generated from `content/freight.json` against the live catalogue on 2026-08-20. Regenerate this sheet whenever `freight.json` changes, or the feed will disagree with what checkout charges.

**Why this exists.** The Google & YouTube sales channel sends one shipping setting for the whole catalogue. About 10% of the yard is not shippable on those terms: ~100 body panels and glass items never ship at any price, and ~767 heavy parts carry a flat freight rate and require a commercial address. Left alone, the feed advertises free delivery on all of them, and Merchant Center treats a delivery promise the checkout refuses to honour as misrepresentation.

Every product type below is an **exact `product_type` value in the catalogue** — paste them straight into the rule builder, no substring matching needed on your side.

---

## Pickup only — 101 products, 27 product types

**Action:** EXCLUDE from the feed entirely

These are never shipped. A shopping listing for a door you will not mail only produces an angry phone call, so the right move is exclusion, not a shipping label. In Merchant Center this is an **exclusion rule**; in the Shopify channel you can also exclude the matching collection from the feed.

> Body panels and glass. We do not ship these at any price — they arrive damaged and cost more to crate than they are worth. Local pickup at the yard only.

| Product type | Products |
|---|---:|
| `Pickup BOX` | 17 |
| `Bumper Assembly, Rear` | 13 |
| `Decklid Tailgate` | 11 |
| `Bumper Assembly, Front` | 9 |
| `DR Assm, Rear Side, Left` | 7 |
| `Door Assembly, FR, Left` | 7 |
| `Door Assembly, FR, Right` | 4 |
| `Quarter Panel Assembly, Left` | 4 |
| `Radiator Core Supp` | 4 |
| `Roof Glass` | 3 |
| `Door Vent Glass, RR, Left` | 2 |
| `Fender, Left` | 2 |
| `Hood` | 2 |
| `Quarter Glass, Left` | 2 |
| `Running Board` | 2 |
| `Back Glass` | 1 |
| `Back Glass Regulato` | 1 |
| `Bumper Filler Panel` | 1 |
| `Bumper Rein, Rear` | 1 |
| `Cowl Vent Panel` | 1 |
| `DR Assm, Rear Side, Right` | 1 |
| `Door Glass, Front, Right` | 1 |
| `Door Vent Glass, FR, Right` | 1 |
| `Fender Flare/ext` | 1 |
| `Hood Hinge, Left` | 1 |
| `Quarter Glass, Right` | 1 |
| `Quarter Panel Assembly, Right` | 1 |

## Freight — oversize — 707 products, 7 product types

**Action:** shipping_label = `freight-oversize`  →  flat $299.99

Attach a $299.99 flat rate to this label in Merchant Center Shipping settings, and note in the listing description that a commercial address is required.

> Engines, transmissions, axles, and K-frames. Ships by LTL freight to a commercial address.

| Product type | Products |
|---|---:|
| `Transmission / Transaxle` | 394 |
| `Engine Assembly` | 249 |
| `Axle Assembly, RR` | 34 |
| `Transmission Transaxle Assembly` | 23 |
| `Axle Assembly, FR (4WD)` | 3 |
| `Susp Crossm K-Frame` | 3 |
| `Short CYL Block` | 1 |

## Freight — heavy — 60 products, 2 product types

**Action:** shipping_label = `freight-heavy`  →  flat $199.99

Attach a $199.99 flat rate to this label. Same commercial-address requirement.

> Transfer cases and differential carriers. Ships by LTL freight to a commercial address.

| Product type | Products |
|---|---:|
| `Transfer Case Assembly` | 44 |
| `Carrier Assembly` | 16 |

## Free ground — 8,458 products, 184 product types

**Action:** none. This is the default and the feed's normal free-shipping setting is correct for it. Listed here only so the four groups add up.

---

## Two things that will bite later

**1. A new product type defaults to free ground.** Classification is substring matching, checked `PICKUP` → `A` → `B`, first hit wins; anything unmatched ships free. So a genuinely new heavy part type arriving from CoreYard silently lands in the free-ground group in both the feed and the checkout. When new types appear, re-run this sheet.

**2. These lists must stay in step with checkout.** `setup_shipping.py` builds the delivery profiles and `tag_shipping.py` writes the `ship:*` tags the theme reads, both from this same `freight.json`. Change the JSON, then re-run both with `--apply`, then regenerate this sheet. Feed, checkout and product page all have to say the same thing.


---
*Generated 2026-08-20 from `content/freight.json` using the `classify()` rule in `scripts/tag_shipping.py` — the same function the store runs on.*
