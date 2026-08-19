#!/usr/bin/env python3
"""Rewrite vehicle tags that carry a doubled make.

`seo.build_tags` used to join make and model directly when a part had no fitment rows,
so the yard's "DODGE TRUCK" / "DODGE 1500 PICKUP" pair became the tag
"2019 Dodge Dodge 1500". That is fixed at the source, but the sync only rewrites a
product when its yard-side state changes, so parts tagged before the fix keep the bad
tag indefinitely — and a doubled make matches nothing, so those parts are invisible to
the storefront's vehicle filter and get no vehicle-matched related parts.

Finds them from the Shopify side (cheap: one paged scan of tags), asks CoreYard what the
tags should be, and rewrites only the products that differ.

    cd ../coreyard && .venv/bin/python ../abmotorsla/scripts/backfill_tags.py --plan
    cd ../coreyard && .venv/bin/python ../abmotorsla/scripts/backfill_tags.py --apply

Needs CoreYard's virtualenv: the yard database is reached over TDS.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
SYNC_REPO = REPO.parent / "coreyard"
ENV = pathlib.Path(os.environ.get("ABM_ENV", SYNC_REPO / ".env"))
API_VERSION = "2026-07"
sys.path.insert(0, str(SYNC_REPO))

_YEAR = re.compile(r"^\d{4}$")
_ALPHA = re.compile(r"^[A-Za-z]+")


def _stem(token: str) -> str:
    """Leading alphabetic run: "Mercedes-Benz" -> "mercedes", "F-150" -> "f"."""
    m = _ALPHA.match(token)
    return m.group(0).lower() if m else ""


def fix_tag(tag: str) -> str | None:
    """Drop a duplicated make token, or None if the tag is fine.

        "2019 Dodge Dodge 1500"                -> "2019 Dodge 1500"
        "2010 Mercedes-Benz Mercedes Sprinter" -> "2010 Mercedes Sprinter"
        "Dodge Dodge 1500"                     -> "Dodge 1500"

    Compares the make token to the one after it by leading alphabetic run, which catches
    the short-form case. A regex backreference cannot: it happily matches
    "2008 Ford F-150" by capturing just "F".

    Editing the tag in place rather than regenerating from the yard matters. Many of
    these parts have no fitment rows any more, so a freshly generated tag set is a bare
    make — rewriting wholesale would trade several specific vehicles for one useless
    label and make the part harder to find, not easier.
    """
    parts = tag.split()
    i = 1 if (parts and _YEAR.match(parts[0])) else 0
    if len(parts) < i + 2:
        return None
    make, nxt = _stem(parts[i]), _stem(parts[i + 1])
    if len(make) < 3 or make != nxt:
        return None
    return " ".join(parts[:i] + parts[i + 1:])


def doubled(tag: str) -> bool:
    return fix_tag(tag) is not None


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


SCAN = """
query($cursor: String) {
  products(first: 250, after: $cursor, query: "status:active") {
    pageInfo { hasNextPage endCursor }
    nodes { id handle tags variants(first: 1) { nodes { sku } } }
  }
}
"""

UPDATE = """
mutation($id: ID!, $tags: [String!]!) {
  productUpdate(product: {id: $id, tags: $tags}) { userErrors { field message } }
}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--plan", action="store_true", help="report only (default)")
    g.add_argument("--apply", action="store_true", help="rewrite the tags")
    ap.add_argument("--limit", type=int, default=0, help="stop after N products (for a trial)")
    args = ap.parse_args()

    cfg = load_env()
    url = f"https://{cfg['SHOPIFY_STORE']}/admin/api/{API_VERSION}/graphql.json"
    headers = {"Content-Type": "application/json",
               "X-Shopify-Access-Token": cfg["SHOPIFY_ADMIN_TOKEN"]}

    print("scanning the catalogue for doubled makes ...")
    affected, scanned, cursor = [], 0, None
    while True:
        data = gql(url, headers, SCAN, {"cursor": cursor})
        if "__errors" in data:
            sys.exit(f"scan failed: {json.dumps(data['__errors'])[:300]}")
        conn = data["products"]
        for n in conn["nodes"]:
            scanned += 1
            bad = [t for t in (n.get("tags") or []) if doubled(t)]
            if bad:
                sku = (n["variants"]["nodes"] or [{}])[0].get("sku") or ""
                affected.append({"id": n["id"], "handle": n["handle"], "sku": sku,
                                 "tags": n["tags"], "bad": bad})
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]

    print(f"  {scanned} products scanned, {len(affected)} carry a doubled make")
    if not affected:
        return
    if args.limit:
        affected = affected[:args.limit]

    changed = 0
    for a in affected:
        fixed, seen = [], set()
        for t in a["tags"]:
            corrected = fix_tag(t) or t
            if corrected.lower() not in seen:
                seen.add(corrected.lower())
                fixed.append(corrected)
        if sorted(fixed) == sorted(a["tags"]):
            continue
        changed += 1
        pairs = [(t, fix_tag(t)) for t in a["bad"]][:2]
        print(f"  {'+' if args.apply else '~'} {a['handle']}  R#{a['sku']}"
              f"  ({len(a['tags'])} -> {len(fixed)} tags)")
        for was, now in pairs:
            print(f"      {was!r} -> {now!r}")
        if len(a["bad"]) > 2:
            print(f"      … and {len(a['bad']) - 2} more")
        if not args.apply:
            continue
        res = gql(url, headers, UPDATE, {"id": a["id"], "tags": fixed})
        errs = (res.get("productUpdate") or {}).get("userErrors") or res.get("__errors")
        if errs:
            print(f"      ! update failed: {json.dumps(errs)[:160]}")
            changed -= 1

    if args.apply:
        print(f"\n{changed} product(s) retagged. "
              f"Re-run scripts/build_vehicles.py so the picker sees the corrected models.")
    else:
        print(f"\nplan only — {changed} product(s) would change; re-run with --apply")


if __name__ == "__main__":
    main()
