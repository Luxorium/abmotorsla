#!/usr/bin/env python3
"""Tag Shopify orders that the yard has invoiced in PowerLink.

eBay's status push fires on the *invoice* event: PowerLink drops an
``ECOMOUTBOUNDQUEUE`` row carrying the ``InvoiceID`` and its own integration pushes
that to eBay. That queue is eBay's transport and explicitly not ours to write to
(WORKORDER_WRITE_SPEC 5.6), so this job takes the same trigger and sends it over the
Shopify API instead.

**It deliberately does not create a fulfillment.** Shopify has no "fulfilled but not
shipped" state — creating a fulfillment closes the fulfillment order, and ShipStation
needs that open to attach the UPS tracking number when it buys the label. Fulfilling
here would leave the buyer with no tracking at all. So the yard's progress is recorded
as tags and a note; ShipStation still owns shipped-state and the tracking email.

Freight and pickup-only orders never pass through ShipStation, so they stay
UNFULFILLED until someone closes them by hand. That is a known, accepted gap.

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
    nodes { id name tags note displayFulfillmentStatus }
  }
}
"""

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

        if not missing and not note_needed:
            print(f"  = {name}  already tagged (work order {wo})")
            continue

        changed += 1
        print(f"  {'+' if args.apply else '~'} {name}  work order {wo}  invoice {row['invoice_no']}"
              f"  [{order['displayFulfillmentStatus']}]")
        if missing:
            print(f"      tags += {missing}")
        if note_needed:
            print(f"      note += {marker!r}")
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

    if not args.apply and changed:
        print(f"\nplan only — {changed} order(s) would change; re-run with --apply")
    elif args.apply:
        print(f"\n{changed} order(s) updated")


if __name__ == "__main__":
    main()
