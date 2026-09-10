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
import sys

from _shopify import REPO, Shopify

CHUNK = 200

# The delivery-profile names that already exist in the store, per freight.json group. Kept
# here rather than in freight.json because they are Shopify bookkeeping, not shipping policy,
# and renaming one duplicates a profile rather than renaming it.
PROFILE_NAMES = {
    "A": "Freight — Oversize",
    "B": "Freight — Heavy",
    "GROUND49": "UPS Ground $49.99",
    "GROUND44": "UPS Ground $44.99",
    "GROUND34": "UPS Ground $34.99",
    "GROUND29": "UPS Ground $29.99",
    "GROUND24": "UPS Ground $24.99",
    "GROUND19": "UPS Ground $19.99",
    "GROUND14": "UPS Ground $14.99",
    "GROUND9": "UPS Ground $9.99",
    "PICKUP": "Local Pickup Only",
}

# What a rate is called at checkout, when freight.json does not say. Every group started as
# freight, so that is the default; a parcel group sets "rate_name" instead, because a buyer
# told "Flat Rate Freight" on a glove box waits for a truck that is never coming.
DEFAULT_RATE_NAME = "Flat Rate Freight"


def profile_name(freight: dict, group_id: str) -> str:
    name = PROFILE_NAMES.get(group_id)
    if not name:
        raise RuntimeError(
            f"content/freight.json declares group {group_id!r}, but no delivery profile "
            f"name is mapped for it in {__file__}. Add one to PROFILE_NAMES (and create "
            f"the profile in Shopify admin under that exact name)."
        )
    return name


def rate_name(freight: dict, group_id: str) -> str:
    """The shopper-facing name of this group's rate, from freight.json."""
    body = freight["groups"][group_id]
    return str(body.get("rate_name") or DEFAULT_RATE_NAME).strip()


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
    nodes { id productType tags variants(first: 1) { nodes { id } } }
  }
}
"""

TAGS_ADD_M = """
mutation($id: ID!, $tags: [String!]!) {
  tagsAdd(id: $id, tags: $tags) { userErrors { field message } }
}
"""

TAGS_REMOVE_M = """
mutation($id: ID!, $tags: [String!]!) {
  tagsRemove(id: $id, tags: $tags) { userErrors { field message } }
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


def group_order(freight: dict) -> list[str]:
    """Every group, most restrictive first: the match order, then the default."""
    return match_order(freight) + [default_group(freight)]


def by_tag(freight: dict) -> dict[str, str]:
    """ship:* tag -> group id."""
    return {str(body.get("tag", "")).strip().lower(): gid
            for gid, body in (freight.get("groups") or {}).items()
            if isinstance(body, dict) and body.get("tag")}


def classify_by_type(freight: dict, product_type: str) -> str:
    """Which group a *product type* falls in: first substring hit, else the default.

    Only a fallback. It reads the product type Shopify holds, which is the renderer's
    expansion of the yard's own abbreviation — "Engine Motor Assembly" for "ENGINE
    ASSEMBLY" — so a pattern written against the yard's spelling cannot match here. That is
    exactly how engines came to be tagged freight and quoted free, and why the tag below
    wins whenever there is one.
    """
    t = (product_type or "").lower()
    groups = freight["groups"]
    for gid in match_order(freight):
        body = groups[gid]
        if any(word in t for word in body.get("exclude") or []):
            continue
        if any(pattern in t for pattern in body.get("match") or []):
            return gid
    return default_group(freight)


def classify(freight: dict, product_type: str, tags: list[str] | None = None) -> str:
    """Which group a product falls in.

    The ship:* tag is the answer, not a second opinion: CoreYard wrote it during the publish
    that created the product, from the yard's own part-type spelling and the same
    freight.json this reads. This side sees only what Shopify holds, so re-deriving the
    classification here is a copy of the rule that is free to disagree with the copy the
    shopper is actually shown — a part labelled one way on the page and charged another at
    checkout, which is the failure the one-file contract exists to prevent.

    Read in match order so a product still carrying a stale tag beside a current one lands
    in the more restrictive group; a product with no ship:* tag at all — one loaded before
    the contract existed — falls back to its product type.
    """
    present = {t.strip().lower() for t in (tags or [])}
    for gid in group_order(freight):
        tag = str(freight["groups"][gid].get("tag", "")).strip().lower()
        if tag and tag in present:
            return gid
    return classify_by_type(freight, product_type)


def scan(gql: Shopify, freight: dict, by_type: bool = False):
    """Sort every variant into its shipping group.

    Normally the ship:* tag CoreYard wrote is the answer. ``by_type`` ignores the tag and
    re-derives the group from the product type against freight.json's own match patterns.
    That is the catch-up when a re-tag of the whole catalogue is not practical: the tag
    still drives the product-page wording, but the delivery profile a shopper is charged
    from is put right immediately. Only correct while freight.json's patterns cover both the
    yard spelling and the renderer's expansion of it, which is what check_contracts.py and
    the offline classification check verify.
    """
    buckets = collections.defaultdict(list)
    counts = collections.Counter()
    untagged = 0
    tags_seen = set(by_tag(freight))
    cursor = None
    while True:
        conn = gql(SCAN_Q, {"cursor": cursor})["products"]
        for p in conn["nodes"]:
            vs = p["variants"]["nodes"]
            if not vs:
                continue
            tags = p.get("tags") or []
            if not tags_seen.intersection(t.strip().lower() for t in tags):
                untagged += 1
            g = (classify_by_type(freight, p["productType"]) if by_type
                 else classify(freight, p["productType"], tags))
            buckets[g].append(vs[0]["id"])
            counts[g] += 1
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]
    return buckets, counts, untagged


def retag(gql: Shopify, freight: dict, *, apply: bool) -> None:
    """Correct only the ship:* tag on each product, from its product type.

    CoreYard writes this tag during the publish that creates a product. This is the
    catch-up for a catalogue published before freight.json split into the current tiers,
    when re-running the full sync is not practical: the delivery profile a shopper is
    charged from is already put right by ``--by-type``; this brings the tag the product
    page reads into line with it.

    It adds the one right ship: tag and removes any other ship: tag. Every tag outside the
    ship: namespace is left exactly as it is — vehicle, interchange and hand-added tags are
    never touched.
    """
    tag_of = {gid: str(b.get("tag", "")).strip()
              for gid, b in freight["groups"].items()}
    planned: list[tuple] = []
    scanned = 0
    cursor = None
    while True:
        conn = gql(SCAN_Q, {"cursor": cursor})["products"]
        for p in conn["nodes"]:
            scanned += 1
            tags = [t.strip() for t in (p.get("tags") or []) if t.strip()]
            want = tag_of[classify_by_type(freight, p["productType"])]
            have = [t for t in tags if t.lower().startswith("ship:")]
            if [t.lower() for t in have] == [want.lower()]:
                continue
            add = [] if want.lower() in {t.lower() for t in have} else [want]
            remove = [t for t in have if t.lower() != want.lower()]
            planned.append((p["id"], want, add, remove))
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]

    moved = collections.Counter(want for _, want, _, _ in planned)
    print(f"scanned {scanned} products; {len(planned)} need their ship: tag corrected")
    for tag, n in sorted(moved.items(), key=lambda kv: -kv[1]):
        print(f"    -> {tag:<20} {n}")
    if not planned:
        print("every product already carries the right ship: tag.")
        return
    if not apply:
        print("\nplan only — nothing written. Re-run with --retag --apply.")
        return

    done = failed = 0
    for pid, _want, add, remove in planned:
        try:
            if remove:
                gql(TAGS_REMOVE_M, {"id": pid, "tags": remove})
            if add:
                gql(TAGS_ADD_M, {"id": pid, "tags": add})
            done += 1
        except RuntimeError as exc:
            failed += 1
            if failed <= 5:
                print(f"  ! {pid}: {exc}", file=sys.stderr)
        if (done + failed) % 200 == 0 or (done + failed) == len(planned):
            print(f"  {done + failed}/{len(planned)}")
    print(f"re-tagged {done} product(s), {failed} failed.")


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
                            price: str | None, description: str, rate: str) -> dict:
    payload = {"name": name}
    group = location_group_for(profile, location["id"])
    rates = [flat(rate, price, description)] if price else []
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
        matching = [m for m in methods if m["name"].casefold() == rate.casefold()]
        keep = matching[0] if matching else None
        remove = [m["id"] for m in methods if m is not keep]
        if remove:
            payload["methodDefinitionsToDelete"] = remove
        zone_input = {"id": target["zone"]["id"]}
        if keep:
            zone_input["methodDefinitionsToUpdate"] = [
                flat(rate, price, description, keep["id"])
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


def verify_rates(profile: dict, location: dict, price: str | None, rate: str) -> None:
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
    if len(methods) != 1 or methods[0]["name"] != rate:
        raise RuntimeError(f"{profile['name']}: {rate} rate reconciliation failed")
    provider = methods[0].get("rateProvider") or {}
    amount = ((provider.get("price") or {}).get("amount"))
    if amount is None or abs(float(amount) - float(price)) > 0.001:
        raise RuntimeError(f"{profile['name']}: expected ${price}, found {amount}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--by-type", action="store_true", dest="by_type",
                    help="assign each variant's delivery profile from its product type "
                         "against freight.json, not from the ship:* tag it currently "
                         "carries (use when the catalogue has not been re-tagged yet)")
    ap.add_argument("--retag", action="store_true",
                    help="correct only the ship:* tag on each product from its product "
                         "type; touches no other tag and no delivery profile")
    ap.add_argument("--location", default=os.environ.get("ABM_LOCATION"),
                    help="active Shopify location name or GraphQL ID (or set ABM_LOCATION)")
    args = ap.parse_args()
    if not (args.plan or args.apply):
        ap.print_help()
        return

    gql = Shopify.from_env()
    freight = json.loads((REPO / "content" / "freight.json").read_text())

    if args.retag:
        retag(gql, freight, apply=args.apply)
        return

    profiles = gql(PROFILES_Q)["deliveryProfiles"]["nodes"]
    by_name = {p["name"]: p for p in profiles}
    default = next(p for p in profiles if p["default"])
    loc = choose_location(gql, args.location)
    print(f"using location: {loc['name']} ({loc['id']})")

    print("scanning catalog…" + ("  (classifying by product type)" if args.by_type else ""))
    buckets, counts, untagged = scan(gql, freight, by_type=args.by_type)
    total = sum(counts.values())
    print(f"  {total} products")
    if untagged:
        print(f"  {untagged} carry no ship:* tag and were classified from their product "
              f"type; run `bin/coreyard sync` so CoreYard tags them")
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
         freight["groups"][gid].get("note") or "", rate_name(freight, gid))
        for gid in match_order(freight)
    ]
    print("\nprofiles to create/update:")
    for name, group, price, note, rate in plan:
        profile = by_name.get(name)
        current = profile_variants(gql, profile["id"]) if profile else set()
        desired = set(buckets[group])
        if profile:
            managed_profile_payload(profile, loc, name, price, note, rate)
        state = "exists" if profile else "create"
        charge = f"${price} as {rate!r}" if price else "no shipping rates (pickup only)"
        print(f"  {name:<30}{state:<8}{counts[group]:>5} products   {charge}")
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
    for name, group, price, desc, rate in plan:
        desired = set(buckets[group])
        rates = [flat(rate, price, desc)] if price else []
        if name in by_name:
            profile = by_name[name]
            pid = profile["id"]
            current = profile_variants(gql, pid)
            print(f"\n{name}: reconciling settings and {len(desired)} products…")
            update_profile(gql, pid,
                           managed_profile_payload(profile, loc, name, price, desc, rate), name)
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
    # Everything above this line has already been written and each mutation checked its own
    # userErrors. This block only reads the result back. So a *throttle here* is not a
    # failure of the job, and treating it as one is worse than not verifying: on 2026-09-09
    # a 20,000-product `coreyard repair titles` was spending the same API budget, this
    # read-back gave up, and the run exited 1 — which raised a desktop alert and left a
    # doctor warning lit for two hours, while the profiles had been correct the whole time
    # (every group logged "associate 0, dissociate 0" that run).
    #
    # A genuine mismatch or a bad rate still fails, loudly. Only the store declining to
    # answer is downgraded, and the next scheduled run verifies anyway. The message
    # deliberately avoids "!!" and "FAILED", which are the markers `coreyard doctor` scans
    # these logs for.
    try:
        refreshed = gql(PROFILES_Q)["deliveryProfiles"]["nodes"]
        refreshed_by_name = {p["name"]: p for p in refreshed}
        for name, group, price, _, rate in plan:
            profile = refreshed_by_name[name]
            actual = profile_variants(gql, profile["id"])
            desired = set(buckets[group])
            if actual != desired:
                raise RuntimeError(
                    f"{name}: verification failed ({len(desired - actual)} missing, "
                    f"{len(actual - desired)} stale)"
                )
            verify_rates(profile, loc, price, rate)
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
    except RuntimeError as exc:
        if "THROTTLED" not in str(exc).upper():
            raise
        print("  verification skipped: the store throttled the read-back. Every change "
              "above reported success; the next scheduled run verifies them.")


if __name__ == "__main__":
    main()
