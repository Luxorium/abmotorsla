#!/usr/bin/env python3
"""Tag Shopify orders that the yard has invoiced in PowerLink.

eBay's status push fires on the *invoice* event: PowerLink drops an
``ECOMOUTBOUNDQUEUE`` row carrying the ``InvoiceID`` and its own integration pushes
that to eBay. That queue is eBay's transport and explicitly not ours to write to
(WORKORDER_WRITE_SPEC 5.6), so this job takes the same trigger and sends it over the
Shopify API instead.

Whether it fulfills depends on how the part ships, because Shopify has no "fulfilled
but not shipped" state — creating a fulfillment closes the fulfillment order, and
ShipStation needs that open to attach the UPS tracking number when it buys the label.

  parcel (ship:free)                tag only. ShipStation fulfills it and sends the
                                    tracking email. Fulfilling here would leave the
                                    buyer with no tracking at all.
  freight-299 / freight-199         tag and fulfill. No UPS label is ever bought for
  pickup-only                       these, so ShipStation never sees them and nothing
                                    else would ever close them out.

A mixed order is left alone: ShipStation still has parcel lines to ship, and closing
the whole order early would cost the buyer their tracking. Fulfillment is created with
notifyCustomer false — the yard decides when a customer hears from it.

The link back to the storefront is ``INVOICE_LINEITEM.WorkOrderID`` -> ``WORKORDER``,
whose ``CustomerPO`` holds the Shopify order name. The invoice's *own* ``CustomerPO`` is
a freeform counter field ("CR-V", "TRANSIT") and is not usable for this.

    python3 scripts/sync_invoiced.py            # plan: show what would change
    python3 scripts/sync_invoiced.py --apply    # write the tags and note

Read-only against PowerLink. Needs write_orders on the Shopify app.
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

REPO = pathlib.Path(__file__).resolve().parent.parent
SYNC_REPO = REPO.parent / "coreyard"
ENV = pathlib.Path(os.environ.get("ABM_ENV", SYNC_REPO / ".env"))
API_VERSION = "2026-07"

# The yard database lives behind CoreYard's TDS client; borrow it rather than
# reimplementing a SQL Server driver here.
sys.path.insert(0, str(SYNC_REPO))

INVOICED = """
SELECT DISTINCT w.CustomerPO AS po, w.WorkOrderNumber AS wo,
       i.InvoiceNumber AS invoice_no,
       ISNULL(CAST(i.OrderSource AS varchar(4)),'') AS src
  FROM dbo.INVOICE_LINEITEM il
  JOIN dbo.INVOICE i     ON i.InvoiceID   = il.InvoiceID
  JOIN dbo.WORKORDER w   ON w.WorkOrderID = il.WorkOrderID AND w.IsLastRevision = 1
 WHERE w.CustomerPO LIKE N'#%'
"""


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


def gql(url: str, headers: dict, query: str, variables: dict | None = None, tries: int = 5) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, data=body, headers=headers), timeout=90
            ) as r:
                payload = json.loads(r.read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            if attempt < tries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
        errors = payload.get("errors") or []
        if errors:
            throttled = any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors)
            if throttled and attempt < tries - 1:
                time.sleep(2 ** attempt)
                continue
            return {"__errors": errors}
        return payload.get("data") or {}
    return {}


def invoiced_orders() -> list[dict]:
    """Storefront work orders PowerLink has invoiced, newest first."""
    from coreyard.yms import db  # noqa: E402  (path set above)

    with db.connect() as conn:
        rows = []
        for r in db.query(conn, INVOICED):
            rows.append(dict(r) if not isinstance(r, (list, tuple)) else r)
    return rows


ORDER_BY_NAME = """
query($q: String!) {
  orders(first: 5, query: $q) {
    nodes {
      id name tags note displayFulfillmentStatus
      lineItems(first: 50) { nodes { id product { tags } } }
      fulfillmentOrders(first: 10) { nodes { id status } }
    }
  }
}
"""

FULFILL = """
mutation($fulfillment: FulfillmentInput!) {
  fulfillmentCreate(fulfillment: $fulfillment) {
    fulfillment { id status }
    userErrors { field message }
  }
}
"""

# How a part ships, from the ship:* tag tag_shipping.py writes. Anything without one
# is parcel, matching snippets/shipping-group.liquid.
NON_PARCEL = ("ship:freight-299", "ship:freight-199", "ship:pickup-only")


def ship_group(tags: list[str]) -> str:
    for t in NON_PARCEL:
        if t in tags:
            return t
    return "ship:free"


def shipstation_will_ship(order: dict) -> bool:
    """True when any line goes out as a parcel, i.e. ShipStation owns this order."""
    lines = (order.get("lineItems") or {}).get("nodes") or []
    groups = {ship_group((li.get("product") or {}).get("tags") or []) for li in lines}
    return (not groups) or ("ship:free" in groups)

TAGS_ADD = """
mutation($id: ID!, $tags: [String!]!) {
  tagsAdd(id: $id, tags: $tags) { userErrors { field message } }
}
"""

ORDER_NOTE = """
mutation($input: OrderInput!) {
  orderUpdate(input: $input) { order { id note } userErrors { field message } }
}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the tags and note (default: plan)")
    args = ap.parse_args()

    cfg = load_env()
    url = f"https://{cfg['SHOPIFY_STORE']}/admin/api/{API_VERSION}/graphql.json"
    headers = {"Content-Type": "application/json",
               "X-Shopify-Access-Token": cfg["SHOPIFY_ADMIN_TOKEN"]}

    rows = invoiced_orders()
    print(f"invoiced storefront work orders in PowerLink: {len(rows)}")
    if not rows:
        print("nothing to do")
        return

    changed = 0
    for row in rows:
        name = str(row["po"]).strip()
        wo = row["wo"]
        want = ["invoiced", f"wo-{wo}"]

        data = gql(url, headers, ORDER_BY_NAME, {"q": f"name:{name}"})
        if "__errors" in data:
            print(f"  ! {name}: lookup failed: {json.dumps(data['__errors'])[:160]}")
            continue
        nodes = [o for o in ((data.get("orders") or {}).get("nodes") or []) if o["name"] == name]
        if not nodes:
            print(f"  ! {name}: no such Shopify order (work order {wo})")
            continue
        order = nodes[0]

        missing = [t for t in want if t not in (order.get("tags") or [])]
        marker = f"PowerLink WO {wo}"
        note = order.get("note") or ""
        note_needed = marker not in note

        # Freight and pickup never reach ShipStation, so nothing else will ever close
        # them. Parcel orders are left open on purpose: fulfilling closes the
        # fulfillment order and ShipStation could no longer attach the tracking number.
        parcel = shipstation_will_ship(order)
        open_fos = [f["id"] for f in (order.get("fulfillmentOrders") or {}).get("nodes") or []
                    if f.get("status") == "OPEN"]
        fulfil = (not parcel) and bool(open_fos) \
            and order.get("displayFulfillmentStatus") != "FULFILLED"

        if not missing and not note_needed and not fulfil:
            state = "parcel, ShipStation owns it" if parcel else "already closed"
            print(f"  = {name}  nothing to do ({state})")
            continue

        changed += 1
        print(f"  {'+' if args.apply else '~'} {name}  work order {wo}  invoice {row['invoice_no']}"
              f"  [{order['displayFulfillmentStatus']}]")
        if missing:
            print(f"      tags += {missing}")
        if note_needed:
            print(f"      note += {marker!r}")
        if fulfil:
            print(f"      fulfil: yes — no parcel line, ShipStation will never see it")
        elif parcel:
            print(f"      fulfil: no — parcel line present, left open for ShipStation")
        if not args.apply:
            continue

        if missing:
            res = gql(url, headers, TAGS_ADD, {"id": order["id"], "tags": missing})
            errs = (res.get("tagsAdd") or {}).get("userErrors") or res.get("__errors")
            if errs:
                print(f"      ! tagging failed: {json.dumps(errs)[:160]}")
                continue
        if note_needed:
            # Append: the buyer's own note at checkout must survive.
            merged = (note + "\n" if note else "") + f"{marker} invoiced"
            res = gql(url, headers, ORDER_NOTE, {"input": {"id": order["id"], "note": merged}})
            errs = (res.get("orderUpdate") or {}).get("userErrors") or res.get("__errors")
            if errs:
                print(f"      ! note failed: {json.dumps(errs)[:160]}")

        if fulfil:
            payload = {"notifyCustomer": False,
                       "lineItemsByFulfillmentOrder": [{"fulfillmentOrderId": fo}
                                                       for fo in open_fos]}
            res = gql(url, headers, FULFILL, {"fulfillment": payload})
            node = (res.get("fulfillmentCreate") or {})
            errs = node.get("userErrors") or res.get("__errors")
            if errs:
                print(f"      ! fulfillment failed: {json.dumps(errs)[:200]}")
            else:
                print(f"      fulfilled: {(node.get('fulfillment') or {}).get('status')}")

    if not args.apply and changed:
        print(f"\nplan only — {changed} order(s) would change; re-run with --apply")
    elif args.apply:
        print(f"\n{changed} order(s) updated")


if __name__ == "__main__":
    main()
