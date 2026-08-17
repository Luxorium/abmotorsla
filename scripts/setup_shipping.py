#!/usr/bin/env python3
"""Configure Shopify shipping to match how the yard actually ships.

Three groups, from content/freight.json:

  PICKUP  body panels and glass — never shipped, pickup or local delivery only
  A       engines, transmissions, axles, K-frames — $299.99 flat rate freight
  B       transfer cases, differential carriers — $199.99 flat rate freight
  ground  everything else (about 90%) — free shipping

The store started with FREIGHT and FREIGHT LIGHT rates sitting on the *default*
profile, which offered $299.99 freight as a choice on a $50 alternator and left
no free-shipping rate at all. This moves each rate onto a profile that holds
only the products it applies to.

    python3 scripts/setup_shipping.py --plan     # classify + show what changes
    python3 scripts/setup_shipping.py --apply
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
ENV = pathlib.Path(os.environ.get("ABM_ENV", REPO.parent / "abmotors-to-shopify" / ".env"))
API_VERSION = "2025-07"
CHUNK = 200


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


class Shopify:
    def __init__(self, store: str, token: str):
        self.url = f"https://{store}/admin/api/{API_VERSION}/graphql.json"
        self.headers = {"Content-Type": "application/json", "X-Shopify-Access-Token": token}

    def __call__(self, query: str, variables: dict | None = None, tries: int = 6) -> dict:
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        for attempt in range(tries):
            try:
                req = urllib.request.Request(self.url, data=body, headers=self.headers)
                with urllib.request.urlopen(req, timeout=120) as r:
                    payload = json.loads(r.read())
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
                if attempt < tries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
            errors = payload.get("errors") or []
            if errors:
                if any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors) and attempt < tries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise RuntimeError(json.dumps(errors)[:500])
            return payload.get("data") or {}
        raise RuntimeError("exhausted retries")


PROFILES_Q = """
{
  deliveryProfiles(first: 20) {
    nodes {
      id
      name
      default
      profileLocationGroups {
        locationGroup { id }
        locationGroupZones(first: 20) {
          nodes {
            zone { id name }
            methodDefinitions(first: 20) { nodes { id name } }
          }
        }
      }
    }
  }
}
"""

SCAN_Q = """
query($cursor: String) {
  products(first: 250, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes { productType variants(first: 1) { nodes { id } } }
  }
}
"""

CREATE = """
mutation($profile: DeliveryProfileInput!) {
  deliveryProfileCreate(profile: $profile) {
    profile { id name }
    userErrors { field message }
  }
}
"""

UPDATE = """
mutation($id: ID!, $profile: DeliveryProfileInput!) {
  deliveryProfileUpdate(id: $id, profile: $profile) {
    profile { id name }
    userErrors { field message }
  }
}
"""


def classify(freight: dict, product_type: str) -> str:
    t = (product_type or "").lower()
    for group in ("PICKUP", "A", "B"):
        for pattern in freight[group]:
            if pattern in t:
                return group
    return "ground"


def scan(gql: Shopify, freight: dict):
    buckets = collections.defaultdict(list)
    counts = collections.Counter()
    cursor = None
    while True:
        conn = gql(SCAN_Q, {"cursor": cursor})["products"]
        for p in conn["nodes"]:
            vs = p["variants"]["nodes"]
            if not vs:
                continue
            g = classify(freight, p["productType"])
            buckets[g].append(vs[0]["id"])
            counts[g] += 1
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]
    return buckets, counts


def zone(name: str, rates: list[dict]) -> dict:
    z = {"name": name, "countries": [{"code": "US", "includeAllProvinces": True}]}
    if rates:
        z["methodDefinitionsToCreate"] = rates
    return z


def flat(name: str, amount: str, description: str) -> dict:
    return {
        "name": name,
        "description": description,
        "active": True,
        "rateDefinition": {"price": {"amount": amount, "currencyCode": "USD"}},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not (args.plan or args.apply):
        ap.print_help()
        return

    cfg = load_env()
    gql = Shopify(cfg["SHOPIFY_STORE"], cfg["SHOPIFY_ADMIN_TOKEN"])
    freight = json.loads((REPO / "content" / "freight.json").read_text())

    profiles = gql(PROFILES_Q)["deliveryProfiles"]["nodes"]
    by_name = {p["name"]: p for p in profiles}
    default = next(p for p in profiles if p["default"])

    loc = gql("{ locations(first:5){ nodes{ id name isActive } } }")["locations"]["nodes"][0]

    print("scanning catalog…")
    buckets, counts = scan(gql, freight)
    total = sum(counts.values())
    print(f"  {total} products")
    for g in ("ground", "A", "B", "PICKUP"):
        label = {"ground": "free ground", "A": "freight $299.99",
                 "B": "freight $199.99", "PICKUP": "pickup only"}[g]
        print(f"    {label:<20}{counts[g]:>6}")

    # What sits on the default profile today?
    stale = []
    for lg in default["profileLocationGroups"]:
        for z in lg["locationGroupZones"]["nodes"]:
            for m in z["methodDefinitions"]["nodes"]:
                if m["name"].upper().startswith("FREIGHT"):
                    stale.append((m["id"], m["name"], z["zone"]["name"]))

    print(f"\ndefault profile {default['name']!r}")
    print(f"  freight rates to remove from it: {[s[1] for s in stale] or 'none'}")
    print("  free shipping rate to add: Free Shipping ($0.00)")

    plan = [
        ("Freight — Oversize", "A", "299.99",
         "Flat Rate Freight. Must ship to a commercial/business address with a forklift."),
        ("Freight — Heavy", "B", "199.99",
         "Flat Rate Freight. Must ship to a commercial/business address with a forklift."),
        ("Pickup Only — Body & Glass", "PICKUP", None,
         "Local pickup in Amite, Louisiana only. These parts are not shipped."),
    ]
    print("\nprofiles to create/update:")
    for name, group, price, _ in plan:
        state = "exists" if name in by_name else "create"
        rate = f"${price}" if price else "no shipping rates (pickup only)"
        print(f"  {name:<30}{state:<8}{counts[group]:>5} products   {rate}")

    if args.plan:
        print("\nplan only — nothing changed")
        return

    # 1. default profile: drop the freight rates, add free shipping
    dz = None
    for lg in default["profileLocationGroups"]:
        for z in lg["locationGroupZones"]["nodes"]:
            dz = z["zone"]
            dlg = lg["locationGroup"]
            break
    payload = {
        "methodDefinitionsToDelete": [s[0] for s in stale],
        "locationGroupsToUpdate": [{
            "id": dlg["id"],
            "zonesToUpdate": [{
                "id": dz["id"],
                "methodDefinitionsToCreate": [
                    flat("Free Shipping", "0.00", "Free shipping, 2-5 business days from Amite, LA.")
                ],
            }],
        }],
    }
    print("\nupdating default profile…")
    res = gql(UPDATE, {"id": default["id"], "profile": payload})["deliveryProfileUpdate"]
    print(f"  {'ERR ' + str(res['userErrors']) if res['userErrors'] else 'free shipping added, freight rates removed'}")

    # 2. the three dedicated profiles
    for name, group, price, desc in plan:
        variants = buckets[group]
        rates = [flat("Flat Rate Freight", price, desc)] if price else []
        if name in by_name:
            pid = by_name[name]["id"]
            print(f"\n{name}: exists, associating {len(variants)} products…")
        else:
            body = {
                "name": name,
                "locationGroupsToCreate": [{
                    "locations": [loc["id"]],
                    "zonesToCreate": [zone("Domestic", rates)] if rates else [],
                }],
            }
            res = gql(CREATE, {"profile": body})["deliveryProfileCreate"]
            if res["userErrors"]:
                print(f"\n{name}: ERR {res['userErrors']}")
                continue
            pid = res["profile"]["id"]
            print(f"\n{name}: created ({len(variants)} products to associate)")

        for i in range(0, len(variants), CHUNK):
            chunk = variants[i:i + CHUNK]
            res = gql(UPDATE, {"id": pid, "profile": {"variantsToAssociate": chunk}})["deliveryProfileUpdate"]
            if res["userErrors"]:
                print(f"  ! {res['userErrors']}")
                break
            print(f"  associated {min(i + CHUNK, len(variants))}/{len(variants)}")

    # 3. local pickup at the yard, required for the pickup-only group to be buyable
    print("\nenabling local pickup at the yard…")
    try:
        res = gql("""
        mutation($id: ID!) {
          locationLocalPickupEnable(localPickupSettings: {
            locationId: $id,
            pickupTime: TWENTY_FOUR_HOURS,
            instructions: "Bring your order number. Counter hours Mon-Fri 8:00 AM - 5:00 PM Central, 59174 Hwy 51, Amite, LA 70422."
          }) { localPickupSettings { instructions } userErrors { field message } }
        }
        """, {"id": loc["id"]})["locationLocalPickupEnable"]
        print(f"  {'ERR ' + str(res['userErrors']) if res['userErrors'] else 'local pickup enabled'}")
    except Exception as exc:
        print(f"  ! could not enable local pickup: {exc}")
        print("    set it by hand: Settings -> Shipping and delivery -> Local pickup")


if __name__ == "__main__":
    main()
