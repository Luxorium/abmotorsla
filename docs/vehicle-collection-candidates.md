# Vehicle collection candidates

Counts are **ACTIVE products** carrying that vehicle's tag, measured against the live catalogue on 2026-08-20. 9,326 active products, 46 makes, 701 make/model pairs with stock.

**What this is for.** The vehicle picker currently sends shoppers to `/collections/all/ford+ford-ranger`, and Shopify's robots.txt disallows every collection URL containing a `+`. So the pages answering *"used parts for a 2014 Ford Ranger"* — the highest-intent query in this business — cannot be crawled. Real collections at clean URLs fix that.

**Do not approve all 701 pairs.** Most would be thin pages, and thin pages hurt more than missing ones. The cut lines below are a proposal; cross off anything you do not want.

---

## Recommended: 39 makes + 53 models = **92 collections**

- **Makes** — every make with at least 10 parts in stock.
- **Models** — every make/model with at least 100 parts in stock.
- **Excluded as not real makes:** American, General Motors, Miscellaneous. These are catch-all buckets in the interchange data, not names anyone searches for.

Each collection needs its own written description. Templated copy with the vehicle name swapped in is worse than no page at all — that is precisely the thin-content pattern Google penalises.

## Makes — approve or strike (39 recommended)

| ✓ | Make | Parts | Proposed URL |
|---|---|---:|---|
| ☐ | Ford | 1,406 | `/collections/ford-parts` |
| ☐ | Chevrolet | 1,303 | `/collections/chevrolet-parts` |
| ☐ | Nissan | 1,227 | `/collections/nissan-parts` |
| ☐ | Toyota | 878 | `/collections/toyota-parts` |
| ☐ | Honda | 827 | `/collections/honda-parts` |
| ☐ | GMC | 637 | `/collections/gmc-parts` |
| ☐ | Dodge | 632 | `/collections/dodge-parts` |
| ☐ | Hyundai | 585 | `/collections/hyundai-parts` |
| ☐ | Buick | 427 | `/collections/buick-parts` |
| ☐ | Mercury | 380 | `/collections/mercury-parts` |
| ☐ | KIA | 359 | `/collections/kia-parts` |
| ☐ | Jeep | 347 | `/collections/jeep-parts` |
| ☐ | Cadillac | 333 | `/collections/cadillac-parts` |
| ☐ | Chrysler | 309 | `/collections/chrysler-parts` |
| ☐ | Lexus | 270 | `/collections/lexus-parts` |
| ☐ | Lincoln | 257 | `/collections/lincoln-parts` |
| ☐ | Infiniti | 228 | `/collections/infiniti-parts` |
| ☐ | Mazda | 228 | `/collections/mazda-parts` |
| ☐ | Pontiac | 206 | `/collections/pontiac-parts` |
| ☐ | Acura | 168 | `/collections/acura-parts` |
| ☐ | Subaru | 163 | `/collections/subaru-parts` |
| ☐ | Volkswagen | 157 | `/collections/volkswagen-parts` |
| ☐ | BMW | 148 | `/collections/bmw-parts` |
| ☐ | Oldsmobile | 124 | `/collections/oldsmobile-parts` |
| ☐ | Isuzu | 121 | `/collections/isuzu-parts` |
| ☐ | Saturn | 117 | `/collections/saturn-parts` |
| ☐ | Audi | 111 | `/collections/audi-parts` |
| ☐ | Mercedes-Benz | 106 | `/collections/mercedes-benz-parts` |
| ☐ | Saab | 99 | `/collections/saab-parts` |
| ☐ | Volvo | 74 | `/collections/volvo-parts` |
| ☐ | Mitsubishi | 50 | `/collections/mitsubishi-parts` |
| ☐ | Mini | 50 | `/collections/mini-parts` |
| ☐ | Scion | 48 | `/collections/scion-parts` |
| ☐ | Suzuki | 33 | `/collections/suzuki-parts` |
| ☐ | Plymouth | 27 | `/collections/plymouth-parts` |
| ☐ | Maybach | 25 | `/collections/maybach-parts` |
| ☐ | Rover | 25 | `/collections/rover-parts` |
| ☐ | Smart | 15 | `/collections/smart-parts` |
| ☐ | Fiat | 10 | `/collections/fiat-parts` |

<details><summary>Below the line — 7 makes not recommended</summary>

| Make | Parts | Why |
|---|---:|---|
| American | 62 | catch-all bucket, not a real make |
| General Motors | 23 | catch-all bucket, not a real make |
| Genesis | 8 | only 8 parts — too thin |
| Jaguar | 6 | only 6 parts — too thin |
| Miscellaneous | 4 | catch-all bucket, not a real make |
| Merkur | 4 | only 4 parts — too thin |
| Porsche | 2 | only 2 parts — too thin |

</details>

## Models — approve or strike (53 recommended)

| ✓ | Vehicle | Parts | Proposed URL |
|---|---|---:|---|
| ☐ | Nissan Altima | 343 | `/collections/nissan-altima-parts` |
| ☐ | Toyota Camry | 325 | `/collections/toyota-camry-parts` |
| ☐ | Chevrolet Malibu | 290 | `/collections/chevrolet-malibu-parts` |
| ☐ | Honda Accord | 284 | `/collections/honda-accord-parts` |
| ☐ | Chevrolet Silverado 1500 | 237 | `/collections/chevrolet-silverado-1500-parts` |
| ☐ | GMC Sierra 1500 | 228 | `/collections/gmc-sierra-1500-parts` |
| ☐ | Ford F150 | 226 | `/collections/ford-f150-parts` |
| ☐ | Ford Fusion | 215 | `/collections/ford-fusion-parts` |
| ☐ | GMC Yukon | 197 | `/collections/gmc-yukon-parts` |
| ☐ | Hyundai Sonata | 194 | `/collections/hyundai-sonata-parts` |
| ☐ | Chevrolet Tahoe | 192 | `/collections/chevrolet-tahoe-parts` |
| ☐ | Chevrolet Suburban 1500 | 192 | `/collections/chevrolet-suburban-1500-parts` |
| ☐ | Dodge 1500 | 190 | `/collections/dodge-1500-parts` |
| ☐ | Honda Civic | 190 | `/collections/honda-civic-parts` |
| ☐ | Chevrolet Equinox | 188 | `/collections/chevrolet-equinox-parts` |
| ☐ | Ford Explorer | 176 | `/collections/ford-explorer-parts` |
| ☐ | Chevrolet Impala | 174 | `/collections/chevrolet-impala-parts` |
| ☐ | Ford Focus | 174 | `/collections/ford-focus-parts` |
| ☐ | Chevrolet Silverado 2500 | 173 | `/collections/chevrolet-silverado-2500-parts` |
| ☐ | Nissan Versa | 166 | `/collections/nissan-versa-parts` |
| ☐ | Chevrolet Silverado 3500 | 164 | `/collections/chevrolet-silverado-3500-parts` |
| ☐ | Ford Escape | 162 | `/collections/ford-escape-parts` |
| ☐ | GMC Sierra 2500 | 162 | `/collections/gmc-sierra-2500-parts` |
| ☐ | GMC Yukon XL 1500 | 161 | `/collections/gmc-yukon-xl-1500-parts` |
| ☐ | GMC Sierra 3500 | 161 | `/collections/gmc-sierra-3500-parts` |
| ☐ | Honda CR-V | 154 | `/collections/honda-cr-v-parts` |
| ☐ | Hyundai Elantra | 136 | `/collections/hyundai-elantra-parts` |
| ☐ | Ford Mustang | 133 | `/collections/ford-mustang-parts` |
| ☐ | Ford Taurus | 133 | `/collections/ford-taurus-parts` |
| ☐ | Toyota Solara | 131 | `/collections/toyota-solara-parts` |
| ☐ | Nissan Maxima | 130 | `/collections/nissan-maxima-parts` |
| ☐ | Toyota Corolla | 129 | `/collections/toyota-corolla-parts` |
| ☐ | Nissan Murano | 124 | `/collections/nissan-murano-parts` |
| ☐ | Dodge 2500 | 122 | `/collections/dodge-2500-parts` |
| ☐ | Dodge 3500 | 121 | `/collections/dodge-3500-parts` |
| ☐ | Nissan Sentra | 120 | `/collections/nissan-sentra-parts` |
| ☐ | Chrysler 300 | 119 | `/collections/chrysler-300-parts` |
| ☐ | Buick Regal | 117 | `/collections/buick-regal-parts` |
| ☐ | Chevrolet Suburban 2500 | 117 | `/collections/chevrolet-suburban-2500-parts` |
| ☐ | Ford Expedition | 116 | `/collections/ford-expedition-parts` |
| ☐ | Cadillac Escalade | 115 | `/collections/cadillac-escalade-parts` |
| ☐ | Toyota RAV4 | 115 | `/collections/toyota-rav4-parts` |
| ☐ | Cadillac CTS | 114 | `/collections/cadillac-cts-parts` |
| ☐ | KIA Optima | 111 | `/collections/kia-optima-parts` |
| ☐ | GMC Terrain | 111 | `/collections/gmc-terrain-parts` |
| ☐ | Buick Lacrosse | 108 | `/collections/buick-lacrosse-parts` |
| ☐ | Nissan Pathfinder | 108 | `/collections/nissan-pathfinder-parts` |
| ☐ | GMC Envoy | 105 | `/collections/gmc-envoy-parts` |
| ☐ | Toyota Avalon | 105 | `/collections/toyota-avalon-parts` |
| ☐ | Chevrolet Cruze | 103 | `/collections/chevrolet-cruze-parts` |
| ☐ | Honda Odyssey | 102 | `/collections/honda-odyssey-parts` |
| ☐ | Nissan Rogue | 101 | `/collections/nissan-rogue-parts` |
| ☐ | Toyota Sienna | 101 | `/collections/toyota-sienna-parts` |

<details><summary>Next 79 models, if you want to go deeper (50–99 parts each)</summary>

| Vehicle | Parts |
|---|---:|
| Cadillac Escalade ESV | 95 |
| Isuzu Ascender | 95 |
| Dodge Charger | 94 |
| Toyota Tundra | 94 |
| Chevrolet Trailblazer | 91 |
| Ford F250SD | 91 |
| Chevrolet Avalanche 1500 | 89 |
| Jeep Grand Cherokee | 89 |
| GMC Yukon XL 2500 | 89 |
| Jeep Compass | 89 |
| Jeep Patriot | 89 |
| Dodge Caravan | 86 |
| Ford F350SD | 85 |
| Mercury Mountaineer | 85 |
| Oldsmobile Bravada | 82 |
| Ford Crown Victoria | 81 |
| Hyundai Santa FE | 81 |
| Buick Rainier | 79 |
| Chevrolet Trailblazer EXT | 79 |
| GMC Sierra Denali 1500 | 78 |
| GMC Envoy XL | 77 |
| Mercury Grand Marquis | 77 |
| Volkswagen Jetta | 72 |
| Lincoln MKZ | 70 |
| Mercury Milan | 70 |
| Chrysler Town & Country | 69 |
| Mercury Sable | 69 |
| Saab 9-7X | 68 |
| Nissan Armada | 68 |
| Chevrolet Camaro | 67 |
| Chrysler 200 | 67 |
| Pontiac G6 | 66 |
| Ford F250 | 66 |
| Chevrolet Traverse | 64 |
| Lincoln Navigator | 64 |
| Toyota Matrix | 64 |
| Volkswagen Passat | 64 |
| GMC Acadia | 62 |
| GMC Sierra Denali | 62 |
| Jeep Cherokee | 62 |
| Hyundai Tucson | 62 |
| Buick Allure | 62 |
| Buick Verano | 62 |
| Dodge Journey | 61 |
| KIA Forte | 60 |
| Cadillac SRX | 59 |
| Chevrolet Monte Carlo | 59 |
| Nissan - Worldwide Micra | 59 |
| Subaru Legacy | 58 |
| GMC Sierra Denali 3500 | 58 |
| Toyota Highlander | 58 |
| Lincoln & Town CAR | 57 |
| Chevrolet Express 2500 VAN | 57 |
| Cadillac XTS | 56 |
| Dodge Avenger | 56 |
| Cadillac Escalade EXT | 55 |
| Lexus ES300 | 55 |
| Mazda 3 | 55 |
| Chrysler Sebring | 55 |
| Ford Ranger | 54 |
| Buick Enclave | 53 |
| Chevrolet Colorado | 53 |
| GMC Envoy XUV | 53 |
| Ford Transit Connect | 53 |
| Dodge Caliber | 53 |
| Mazda CX-5 | 53 |
| GMC Sierra Denali 2500 | 53 |
| Dodge Challenger | 52 |
| Lincoln LT | 52 |
| Honda Pilot | 52 |
| GMC Savana 2500 VAN | 52 |
| Infiniti G35 | 51 |
| Lexus ES350 | 51 |
| Hyundai Azera | 51 |
| GMC Canyon | 50 |
| Dodge Durango | 50 |
| Mini Cooper | 50 |
| Chevrolet Orlando | 50 |
| Ford E350 VAN | 50 |

</details>

---

## After you approve

1. The approved list goes into `content/site.json` as `vehicle_collections`, each with its own description.
2. `store_setup.py --collections --plan` shows exactly what would be created, for a second look.
3. Only then `--apply`.
4. The picker and the collection facet get repointed at the new URLs, so one URL serves the shopper and the crawler.

Once `ford-ranger-parts` is a real collection, narrowing to a year gives `/collections/ford-ranger-parts/1994-ford-ranger` — a single tag, no `+`, so it is no longer robots-blocked. Those year views are set to `noindex,follow`: useful to a shopper, kept out of the index so 700 vehicles × 20 years cannot bloat it.

