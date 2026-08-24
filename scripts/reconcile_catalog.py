#!/usr/bin/env python3
"""Make the Shopify catalogue match the listable half of PowerLink.

Listable means what the storefront promises: priced, in stock, not blocked from online
sale, and carrying at least one photo. The site says "you buy the part in the picture"
in four places, so a part with no photo is deliberately not listed.

The sync cannot do this on its own. Its diff compares the yard against its own state
file, never against what Shopify actually holds, so a part the state calls "synced" is
invisible to it forever — which is how ~16k rows came to be marked done while Shopify
held 10k products. This asks both sides directly and reports four buckets:

    publish   listable, sitting in Shopify as DRAFT
    revive    listable, but ARCHIVED in Shopify
    create    listable, absent from Shopify altogether
    retire    ACTIVE in Shopify, no longer listable in the yard

`retire` zeroes the part's stock before it archives, so a part pulled offline is
unbuyable by quantity as well as by status and cannot come back carrying stale stock.

    python3 scripts/reconcile_catalog.py            # plan
    python3 scripts/reconcile_catalog.py --apply    # publish / revive / archive
    python3 scripts/reconcile_catalog.py --apply --no-retire

`create` is only ever reported, never done here: making a product needs photos, weights
and shipping tags, which is run_sync's job. Read-only against PowerLink.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request
import uuid

REPO = pathlib.Path(__file__).resolve().parent.parent
SYNC_REPO = REPO.parent / "coreyard"
ENV = pathlib.Path(os.environ.get("ABM_ENV", SYNC_REPO / ".env"))
API_VERSION = "2026-07"
sys.path.insert(0, str(SYNC_REPO))

# Refuse to archive more than this share of the live catalogue in one run. A yard
# database that answers slowly or partially must not empty the storefront.
MAX_RETIRE_FRACTION = 0.10


def load_env() -> dict:
    if not ENV.exists():
        sys.exit(f"no .env at {ENV}")
    cfg = {}
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def gql(url, headers, query, variables=None, tries=6):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, data=body, headers=headers), timeout=120
            ) as r:
                payload = json.loads(r.read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            if attempt < tries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
        errors = payload.get("errors") or []
        if errors:
            if any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors) \
                    and attempt < tries - 1:
                time.sleep(2 ** attempt)
                continue
            return {"__errors": errors}
        return payload.get("data") or {}
    return {}


def listable_r_numbers() -> set[str]:
    """Priced, available, unblocked and photographed — straight from the yard."""
    from coreyard.yms import schema, db  # noqa: E402

    m = schema.load()
    expr = m.select["r_number"]
    sql = (f"SELECT {expr} AS r FROM {m.source} "
           f"WHERE {m.scope} AND {m.images_filter}")
    out: set[str] = set()
    with db.connect() as conn:
        for row in db.query(conn, sql):
            val = (dict(row).get("r") if not isinstance(row, (list, tuple)) else row[0])
            if val is not None:
                out.add(str(val).strip())
    return out


SCAN = """
query($cursor: String) {
  products(first: 250, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      status
      variants(first: 1) {
        nodes { sku inventoryQuantity inventoryItem { id tracked } }
      }
    }
  }
}
"""

SET_STATUS = """
mutation($id: ID!, $status: ProductStatus!) {
  productUpdate(product: {id: $id, status: $status}) { userErrors { field message } }
}
"""

LOCATION = """{ locations(first: 1, query: "active:true") { nodes { id name } } }"""

SET_QUANTITIES = """
mutation($input: InventorySetQuantitiesInput!, $idempotencyKey: String!) {
  inventorySetQuantities(input: $input) @idempotent(key: $idempotencyKey) {
    userErrors { field message }
  }
}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the status changes")
    ap.add_argument("--no-retire", action="store_true", help="publish and revive only")
    ap.add_argument("--force-retire", action="store_true",
                    help="archive even beyond the safety fraction")
    args = ap.parse_args()

    cfg = load_env()
    url = f"https://{cfg['SHOPIFY_STORE']}/admin/api/{API_VERSION}/graphql.json"
    headers = {"Content-Type": "application/json",
               "X-Shopify-Access-Token": cfg["SHOPIFY_ADMIN_TOKEN"]}

    print("asking the yard which parts are listable ...")
    listable = listable_r_numbers()
    print(f"  {len(listable)} priced, in stock, unblocked, photographed")

    print("asking Shopify what it holds ...")
    shop: dict[str, dict] = {}
    cursor = None
    while True:
        data = gql(url, headers, SCAN, {"cursor": cursor})
        if "__errors" in data:
            sys.exit(f"scan failed: {json.dumps(data['__errors'])[:300]}")
        conn = data["products"]
        for n in conn["nodes"]:
            v = n["variants"]["nodes"]
            sku = (v[0].get("sku") or "").strip() if v else ""
            if sku:
                item = v[0].get("inventoryItem") or {}
                shop[sku] = {"id": n["id"], "status": n["status"],
                             "inventory_item": item.get("id"),
                             "tracked": item.get("tracked"),
                             "qty": v[0].get("inventoryQuantity")}
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]
    by_status: dict[str, int] = {}
    for v in shop.values():
        by_status[v["status"]] = by_status.get(v["status"], 0) + 1
    print(f"  {len(shop)} products with a SKU  {by_status}")

    publish = sorted(s for s in listable
                     if s in shop and shop[s]["status"] == "DRAFT")
    revive = sorted(s for s in listable
                    if s in shop and shop[s]["status"] == "ARCHIVED")
    create = sorted(s for s in listable if s not in shop)
    retire = sorted(s for s, v in shop.items()
                    if v["status"] == "ACTIVE" and s not in listable)

    active_now = by_status.get("ACTIVE", 0)
    print(f"\n  publish (DRAFT -> ACTIVE)    {len(publish)}")
    print(f"  revive  (ARCHIVED -> ACTIVE) {len(revive)}")
    print(f"  create  (absent; run_sync)   {len(create)}")
    print(f"  retire  (ACTIVE -> ARCHIVED) {len(retire)}")
    for label, ids in (("publish", publish), ("revive", revive),
                       ("create", create), ("retire", retire)):
        if ids:
            print(f"    {label} sample: {ids[:8]}")

    if retire and not args.no_retire and active_now:
        share = len(retire) / active_now
        if share > MAX_RETIRE_FRACTION and not args.force_retire:
            print(f"\n  REFUSING to retire {len(retire)} of {active_now} active "
                  f"({share:.1%} > {MAX_RETIRE_FRACTION:.0%}). A partial answer from the "
                  f"yard looks exactly like this. Re-run with --force-retire if it is real.")
            retire = []

    if not args.apply:
        todo = len(publish) + len(revive) + (0 if args.no_retire else len(retire))
        print(f"\nplan only — {todo} status change(s); re-run with --apply")
        return

    def set_status(skus, status, label):
        done = 0
        for s in skus:
            res = gql(url, headers, SET_STATUS, {"id": shop[s]["id"], "status": status})
            errs = (res.get("productUpdate") or {}).get("userErrors") or res.get("__errors")
            if errs:
                print(f"    ! {s}: {json.dumps(errs)[:120]}")
            else:
                done += 1
        print(f"  {label}: {done}/{len(skus)}")

    def zero_stock(skus):
        """Take the stock off a part before it is archived. The order matters.

        Archiving alone hides a product but leaves its quantity sitting on it, so the
        part is unbuyable only for as long as the status holds. Anything that puts it
        back to ACTIVE -- the revive bucket above, or a click in the admin -- returns it
        to sale carrying a number nobody rechecked against the yard. Zeroing first makes
        it unbuyable by stock as well, which is what run_sync's retire() does.

        Unlike retire(), a failure here does not stop the archive. A part that could not
        be zeroed is still safer hidden than left ACTIVE with stock on it.
        """
        nodes = (gql(url, headers, LOCATION).get("locations") or {}).get("nodes") or []
        if not nodes:
            print("    ! no active location; archiving without zeroing the stock")
            return
        location_id = nodes[0]["id"]
        done = 0
        for s in skus:
            prod = shop[s]
            if not (prod.get("inventory_item") and prod.get("tracked")
                    and (prod.get("qty") or 0)):
                continue
            res = gql(url, headers, SET_QUANTITIES, {
                "idempotencyKey": str(uuid.uuid4()),
                "input": {
                    "name": "available",
                    "reason": "correction",
                    "quantities": [{
                        "inventoryItemId": prod["inventory_item"],
                        "locationId": location_id,
                        "quantity": 0,
                        # Required by 2026-07 even when deliberately opting out of
                        # compare-and-swap: the yard decides what is available, not
                        # Shopify's previously cached number.
                        "changeFromQuantity": None,
                    }],
                },
            })
            errs = ((res.get("inventorySetQuantities") or {}).get("userErrors")
                    or res.get("__errors"))
            if errs:
                print(f"    ! {s}: stock not zeroed: {json.dumps(errs)[:120]}")
            else:
                done += 1
        if done:
            print(f"  zeroed: {done}")

    if publish:
        set_status(publish, "ACTIVE", "published")
    if revive:
        set_status(revive, "ACTIVE", "revived")
    if retire and not args.no_retire:
        zero_stock(retire)
        set_status(retire, "ARCHIVED", "archived")
    if create:
        print(f"\n  {len(create)} listable part(s) are absent from Shopify. "
              f"Those need run_sync to create them with photos, weights and ship tags.")
    print("\nRe-run scripts/build_vehicles.py if the live set changed materially.")


if __name__ == "__main__":
    main()
