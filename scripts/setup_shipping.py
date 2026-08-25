#!/usr/bin/env python3
"""Configure Shopify shipping to match how the yard actually ships.

Every group, its rate and the parts it covers come from content/freight.json — the one file
that decides shipping. CoreYard reads the same file to put the ship:* tag on each product,
so what checkout charges and what the product page promises cannot disagree.

This owns the Shopify side: which delivery profile each variant belongs to, and what each
profile charges. That is storefront configuration, which is why it lives here and not in the
backend.

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

from _shopify import REPO, Shopify

CHUNK = 200

# The delivery-profile names that already exist in the store, per freight.json group. Kept
# here rather than in freight.json because they are Shopify bookkeeping, not shipping policy,
# and renaming one duplicates a profile rather than renaming it.
PROFILE_NAMES = {
    "A": "Freight — Oversize",
    "B": "Freight — Heavy",
    "PICKUP": "Local Pickup Only",
}


def profile_name(freight: dict, group_id: str) -> str:
    name = PROFILE_NAMES.get(group_id)
    if not name:
        raise RuntimeError(
            f"content/freight.json declares group {group_id!r}, but no delivery profile "
            f"name is mapped for it in {__file__}. Add one to PROFILE_NAMES (and create "
            f"the profile in Shopify admin under that exact name)."
        )
    return name


PROFILES_Q = """
{
  deliveryProfiles(first: 20) {
    nodes {
      id
      name
      default
      profileLocationGroups {
        locationGroup {
          id
          locations(first: 50) { nodes { id name isActive } }
        }
        locationGroupZones(first: 20) {
          nodes {
            zone { id name }
            methodDefinitions(first: 20) {
              nodes {
                id name active description
                rateProvider {
                  __typename
                  ... on DeliveryRateDefinition {
                    price { amount currencyCode }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""

PROFILE_ITEMS_Q = """
query($id: ID!, $cursor: String) {
  deliveryProfile(id: $id) {
    profileItems(first: 250, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes { variants(first: 100) { nodes { id } } }
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


def match_order(freight: dict) -> list[str]:
    """Group ids to try, in order. Same reading CoreYard's shipping policy takes."""
    order = freight.get("match_order") or []
    groups = freight.get("groups") or {}
    return [g for g in order if g in groups]


def default_group(freight: dict) -> str:
    """The group that catches everything unmatched."""
    for gid, body in (freight.get("groups") or {}).items():
        if isinstance(body, dict) and body.get("default"):
            return gid
    raise RuntimeError("content/freight.json has no group marked \"default\": true")


def classify(freight: dict, product_type: str) -> str:
    """Which group a part type falls in.

    Deliberately the same rule CoreYard applies when it writes the ship:* tag — first
    substring hit in declared order, else the default. If these two ever disagree, a part is
    labelled one way on the page and charged another at checkout.
    """
    t = (product_type or "").lower()
    groups = freight["groups"]
    for gid in match_order(freight):
        for pattern in groups[gid].get("match") or []:
            if pattern in t:
                return gid
    return default_group(freight)


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


def flat(name: str, amount: str, description: str, method_id: str | None = None) -> dict:
    method = {
        "name": name,
        "description": description,
        "active": True,
        "rateDefinition": {"price": {"amount": amount, "currencyCode": "USD"}},
    }
    if method_id:
        method["id"] = method_id
    return method


def choose_location(gql: Shopify, selector: str | None) -> dict:
    nodes = gql("{ locations(first:50){ nodes{ id name isActive } } }")["locations"]["nodes"]
    active = [loc for loc in nodes if loc["isActive"]]
    if selector:
        wanted = selector.casefold()
        matches = [
            loc for loc in active
            if loc["id"].casefold() == wanted or loc["name"].casefold() == wanted
        ]
        if len(matches) == 1:
            return matches[0]
        choices = ", ".join(f"{loc['name']} ({loc['id']})" for loc in active) or "none"
        sys.exit(f"--location did not identify one active location; active locations: {choices}")
    if len(active) == 1:
        return active[0]
    choices = ", ".join(f"{loc['name']} ({loc['id']})" for loc in active) or "none"
    sys.exit(f"expected one active location; pass --location NAME_OR_ID. Active locations: {choices}")


def all_zones(profile: dict) -> list[dict]:
    return [
        z
        for group in profile["profileLocationGroups"]
        for z in group["locationGroupZones"]["nodes"]
    ]


def location_group_for(profile: dict, location_id: str) -> dict | None:
    groups = profile["profileLocationGroups"]
    for group in groups:
        locations = group["locationGroup"]["locations"]["nodes"]
        if any(loc["id"] == location_id for loc in locations):
            return group
    if len(groups) == 1:
        return groups[0]
    if not groups:
        return None
    raise RuntimeError(f"{profile['name']}: selected location is not in any unambiguous location group")


def profile_has_location(profile: dict, location_id: str) -> bool:
    return any(
        loc["id"] == location_id
        for group in profile["profileLocationGroups"]
        for loc in group["locationGroup"]["locations"]["nodes"]
    )


def domestic_zone(group: dict) -> dict | None:
    zones = group["locationGroupZones"]["nodes"]
    named = [z for z in zones if z["zone"]["name"].casefold() == "domestic"]
    if len(named) == 1:
        return named[0]
    if len(zones) == 1:
        return zones[0]
    if not zones:
        return None
    raise RuntimeError("could not identify one Domestic delivery zone")


def profile_variants(gql: Shopify, profile_id: str) -> set[str]:
    variants = set()
    cursor = None
    while True:
        profile = gql(PROFILE_ITEMS_Q, {"id": profile_id, "cursor": cursor})["deliveryProfile"]
        conn = profile["profileItems"]
        for item in conn["nodes"]:
            variants.update(v["id"] for v in item["variants"]["nodes"])
        if not conn["pageInfo"]["hasNextPage"]:
            return variants
        cursor = conn["pageInfo"]["endCursor"]


def update_profile(gql: Shopify, profile_id: str, payload: dict, label: str) -> None:
    res = gql(UPDATE, {"id": profile_id, "profile": payload})["deliveryProfileUpdate"]
    if res["userErrors"]:
        raise RuntimeError(f"{label}: {res['userErrors']}")


def reconcile_variants(gql: Shopify, profile_id: str, desired: set[str], current: set[str]) -> None:
    changes = (
        ("variantsToDissociate", sorted(current - desired), "dissociated"),
        ("variantsToAssociate", sorted(desired - current), "associated"),
    )
    for field, variants, verb in changes:
        for i in range(0, len(variants), CHUNK):
            chunk = variants[i:i + CHUNK]
            update_profile(gql, profile_id, {field: chunk}, field)
            print(f"  {verb} {min(i + CHUNK, len(variants))}/{len(variants)}")


def managed_profile_payload(profile: dict, location: dict, name: str,
                            price: str | None, description: str) -> dict:
    payload = {"name": name}
    group = location_group_for(profile, location["id"])
    rates = [flat("Flat Rate Freight", price, description)] if price else []
    if group is None:
        create = {"locations": [location["id"]]}
        if rates:
            create["zonesToCreate"] = [zone("Domestic", rates)]
        payload["locationGroupsToCreate"] = [create]
        return payload

    group_input = {"id": group["locationGroup"]["id"]}
    locations = group["locationGroup"]["locations"]["nodes"]
    if not any(loc["id"] == location["id"] for loc in locations):
        group_input["locationsToAdd"] = [location["id"]]

    zones = all_zones(profile)
    if price is None:
        if zones:
            payload["zonesToDelete"] = [z["zone"]["id"] for z in zones]
        if len(group_input) > 1:
            payload["locationGroupsToUpdate"] = [group_input]
        return payload

    target = domestic_zone(group)
    extras = [z["zone"]["id"] for z in zones if z is not target]
    if extras:
        payload["zonesToDelete"] = extras
    if target is None:
        group_input["zonesToCreate"] = [zone("Domestic", rates)]
    else:
        methods = target["methodDefinitions"]["nodes"]
        matching = [m for m in methods if m["name"].casefold() == "flat rate freight"]
        keep = matching[0] if matching else None
        remove = [m["id"] for m in methods if m is not keep]
        if remove:
            payload["methodDefinitionsToDelete"] = remove
        zone_input = {"id": target["zone"]["id"]}
        if keep:
            zone_input["methodDefinitionsToUpdate"] = [
                flat("Flat Rate Freight", price, description, keep["id"])
            ]
        else:
            zone_input["methodDefinitionsToCreate"] = rates
        group_input["zonesToUpdate"] = [zone_input]
    payload["locationGroupsToUpdate"] = [group_input]
    return payload


def default_profile_payload(profile: dict, location: dict) -> dict:
    description = "Free shipping, 2-5 business days from Amite, LA."
    group = location_group_for(profile, location["id"])
    if group is None:
        raise RuntimeError("default delivery profile has no location group")
    target = domestic_zone(group)
    group_input = {"id": group["locationGroup"]["id"]}
    if not profile_has_location(profile, location["id"]):
        group_input["locationsToAdd"] = [location["id"]]
    if target is None:
        group_input["zonesToCreate"] = [zone("Domestic", [flat("Free Shipping", "0.00", description)])]
        return {"locationGroupsToUpdate": [group_input]}

    methods = target["methodDefinitions"]["nodes"]
    free = [m for m in methods if m["name"].casefold() == "free shipping"]
    keep = free[0] if free else None
    remove = [
        m["id"]
        for z in all_zones(profile)
        for m in z["methodDefinitions"]["nodes"]
        if m["name"].upper().startswith("FREIGHT")
    ]
    remove.extend(m["id"] for m in free[1:])
    zone_input = {"id": target["zone"]["id"]}
    if keep:
        zone_input["methodDefinitionsToUpdate"] = [
            flat("Free Shipping", "0.00", description, keep["id"])
        ]
    else:
        zone_input["methodDefinitionsToCreate"] = [flat("Free Shipping", "0.00", description)]
    payload = {"locationGroupsToUpdate": [{**group_input, "zonesToUpdate": [zone_input]}]}
    if remove:
        payload["methodDefinitionsToDelete"] = sorted(set(remove))
    return payload


def verify_rates(profile: dict, location: dict, price: str | None) -> None:
    if not profile_has_location(profile, location["id"]):
        raise RuntimeError(f"{profile['name']}: selected location is not assigned")
    zones = all_zones(profile)
    if price is None:
        if zones:
            raise RuntimeError(f"{profile['name']}: pickup-only profile still has shipping zones")
        return
    if len(zones) != 1:
        raise RuntimeError(f"{profile['name']}: expected one shipping zone, found {len(zones)}")
    methods = zones[0]["methodDefinitions"]["nodes"]
    if len(methods) != 1 or methods[0]["name"] != "Flat Rate Freight":
        raise RuntimeError(f"{profile['name']}: freight rate reconciliation failed")
    provider = methods[0].get("rateProvider") or {}
    amount = ((provider.get("price") or {}).get("amount"))
    if amount is None or abs(float(amount) - float(price)) > 0.001:
        raise RuntimeError(f"{profile['name']}: expected ${price}, found {amount}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--location", default=os.environ.get("ABM_LOCATION"),
                    help="active Shopify location name or GraphQL ID (or set ABM_LOCATION)")
    args = ap.parse_args()
    if not (args.plan or args.apply):
        ap.print_help()
        return

    gql = Shopify.from_env()
    freight = json.loads((REPO / "content" / "freight.json").read_text())

    profiles = gql(PROFILES_Q)["deliveryProfiles"]["nodes"]
    by_name = {p["name"]: p for p in profiles}
    default = next(p for p in profiles if p["default"])
    loc = choose_location(gql, args.location)
    print(f"using location: {loc['name']} ({loc['id']})")

    print("scanning catalog…")
    buckets, counts = scan(gql, freight)
    total = sum(counts.values())
    print(f"  {total} products")
    for gid, body in freight["groups"].items():
        price = body.get("price")
        rate = f"${price}" if price and price != "0.00" else (
            "free" if price == "0.00" else "not shipped")
        print(f"    {body.get('label', gid):<24}{rate:<12}{counts[gid]:>6}")

    # The rates, the names and which parts they cover all come from freight.json. Restating
    # them here is how the profile that charges and the tag that promises drift apart.
    #
    # PROFILE NAMES MUST MATCH THE STORE. Profiles are looked up by name, so a prettier one
    # silently *creates a duplicate* and splits its products across the two. The names are
    # admin-internal — shoppers see rate names — so matching what exists costs nothing.
    plan = [
        (profile_name(freight, gid), gid, freight["groups"][gid].get("price"),
         freight["groups"][gid].get("note") or "")
        for gid in match_order(freight)
    ]
    print("\nprofiles to create/update:")
    for name, group, price, _ in plan:
        profile = by_name.get(name)
        current = profile_variants(gql, profile["id"]) if profile else set()
        desired = set(buckets[group])
        if profile:
            managed_profile_payload(profile, loc, name, price, _)
        state = "exists" if profile else "create"
        rate = f"${price}" if price else "no shipping rates (pickup only)"
        print(f"  {name:<30}{state:<8}{counts[group]:>5} products   {rate}")
        print(f"    associate {len(desired - current)}, dissociate {len(current - desired)}")

    default_rates = [
        m["name"]
        for z in all_zones(default)
        for m in z["methodDefinitions"]["nodes"]
    ]
    print(f"\ndefault profile {default['name']!r}")
    print(f"  reconcile one Free Shipping rate; existing rates: {default_rates or 'none'}")
    default_profile_payload(default, loc)

    if args.plan:
        print("\nplan only — nothing changed")
        return

    # Local pickup must work before pickup-only variants enter a profile with no rates.
    print("\nenabling local pickup at the yard…")
    res = gql("""
    mutation($id: ID!) {
      locationLocalPickupEnable(localPickupSettings: {
        locationId: $id,
        pickupTime: TWENTY_FOUR_HOURS,
        instructions: "Bring your order number. Counter hours Mon-Fri 8:00 AM - 5:00 PM Central, 59174 Hwy 51, Amite, LA 70422."
      }) { localPickupSettings { instructions } userErrors { field message } }
    }
    """, {"id": loc["id"]})["locationLocalPickupEnable"]
    if res["userErrors"]:
        raise RuntimeError(f"local pickup: {res['userErrors']}")
    print("  local pickup enabled")

    # Reconcile restrictive profiles before changing the default profile.
    for name, group, price, desc in plan:
        desired = set(buckets[group])
        rates = [flat("Flat Rate Freight", price, desc)] if price else []
        if name in by_name:
            profile = by_name[name]
            pid = profile["id"]
            current = profile_variants(gql, pid)
            print(f"\n{name}: reconciling settings and {len(desired)} products…")
            update_profile(gql, pid, managed_profile_payload(profile, loc, name, price, desc), name)
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
                raise RuntimeError(f"{name}: {res['userErrors']}")
            pid = res["profile"]["id"]
            current = set()
            print(f"\n{name}: created")
        reconcile_variants(gql, pid, desired, current)

    print("\nreconciling default Free Shipping rate…")
    update_profile(gql, default["id"], default_profile_payload(default, loc), default["name"])

    print("\nverifying final delivery profiles…")
    refreshed = gql(PROFILES_Q)["deliveryProfiles"]["nodes"]
    refreshed_by_name = {p["name"]: p for p in refreshed}
    for name, group, price, _ in plan:
        profile = refreshed_by_name[name]
        actual = profile_variants(gql, profile["id"])
        desired = set(buckets[group])
        if actual != desired:
            raise RuntimeError(
                f"{name}: verification failed ({len(desired - actual)} missing, "
                f"{len(actual - desired)} stale)"
            )
        verify_rates(profile, loc, price)
        print(f"  {name}: {len(actual)} products, rates correct")

    refreshed_default = next(p for p in refreshed if p["default"])
    target = domestic_zone(location_group_for(refreshed_default, loc["id"]))
    free = [m for m in target["methodDefinitions"]["nodes"] if m["name"] == "Free Shipping"]
    stale = [
        m for z in all_zones(refreshed_default)
        for m in z["methodDefinitions"]["nodes"]
        if m["name"].upper().startswith("FREIGHT")
    ]
    if len(free) != 1 or stale:
        raise RuntimeError("default profile rate verification failed")
    print("  default profile: one Free Shipping rate, no stale freight rates")


if __name__ == "__main__":
    main()
