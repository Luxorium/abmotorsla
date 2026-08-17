#!/usr/bin/env python3
"""Listing-quality audit. Read-only. Answers "is the catalog actually ready?"

Checks every product for the things that cost sales or embarrass you in public:
missing SKU, no description, no SEO meta, no tags, zero/absurd price, zero
weight, zero inventory while ACTIVE, no photos, missing alt text, junk titles,
and duplicate SKUs.

    python3 scripts/audit_listings.py
    python3 scripts/audit_listings.py --show 20   # sample offenders per finding
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
            if any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors) and attempt < tries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(json.dumps(errors)[:400])
        return payload.get("data") or {}
    raise RuntimeError("exhausted retries")


QUERY = """query($cursor:String){
  products(first:100, after:$cursor){
    pageInfo{ hasNextPage endCursor }
    nodes{
      id title handle status productType vendor tags
      descriptionHtml
      seo{ title description }
      totalInventory
      mediaCount{ count }
      media(first:1){ nodes{ alt } }
      variants(first:1){ nodes{ id sku price
        inventoryItem{ measurement{ weight{ value } } } } }
    }
  }
}"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", type=int, default=6)
    args = ap.parse_args()

    cfg = load_env()
    url = f"https://{cfg['SHOPIFY_STORE']}/admin/api/{API_VERSION}/graphql.json"
    headers = {"Content-Type": "application/json", "X-Shopify-Access-Token": cfg["SHOPIFY_ADMIN_TOKEN"]}

    findings: dict[str, list[str]] = collections.defaultdict(list)
    skus = collections.Counter()
    n = 0
    cursor = None

    print("scanning…", end="", flush=True)
    while True:
        conn = gql(url, headers, QUERY, {"cursor": cursor})["products"]
        for p in conn["nodes"]:
            n += 1
            label = f"{p['title'][:56]} [{p['handle']}]"
            v = (p["variants"]["nodes"] or [{}])[0]
            sku = (v.get("sku") or "").strip()
            price = float(v.get("price") or 0)
            weight = (((v.get("inventoryItem") or {}).get("measurement") or {}).get("weight") or {}).get("value") or 0
            media = (p.get("mediaCount") or {}).get("count", 0)
            alt = ((p.get("media") or {}).get("nodes") or [{}])[0].get("alt")
            desc = (p.get("descriptionHtml") or "").strip()
            seo = p.get("seo") or {}
            title = p["title"]

            if not sku:
                findings["no stock number (SKU)"].append(label)
            else:
                skus[sku] += 1
            if price <= 0:
                findings["price is zero"].append(label)
            if price < 5:
                findings["price under $5"].append(f"{label} (${price:.2f})")
            if not weight:
                findings["no shipping weight"].append(label)
            if media == 0:
                findings["no photos"].append(label)
            if not alt:
                findings["photo missing alt text"].append(label)
            if len(desc) < 120:
                findings["description thin or empty"].append(label)
            if not (seo.get("title") or "").strip():
                findings["no SEO title"].append(label)
            if not (seo.get("description") or "").strip():
                findings["no SEO description"].append(label)
            if not (p.get("productType") or "").strip():
                findings["no product type"].append(label)
            if not (p.get("vendor") or "").strip():
                findings["no vendor"].append(label)
            if len(p.get("tags") or []) < 3:
                findings["fewer than 3 tags"].append(label)
            if p["status"] == "ACTIVE" and not (p.get("totalInventory") or 0):
                findings["ACTIVE but zero inventory"].append(label)
            if len(title) < 25 or "example" in title.lower() or "test" in title.lower():
                findings["suspicious title"].append(label)
            if len(title) > 255:
                findings["title over 255 chars"].append(label)

        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]
        if n % 1000 < 100:
            print(".", end="", flush=True)

    dupes = {s: c for s, c in skus.items() if c > 1}

    print(f"\n\n{n} products audited\n")
    print(f"{'finding':<34}{'count':>7}{'% of catalog':>14}")
    print("-" * 55)
    ordered = sorted(findings.items(), key=lambda kv: -len(kv[1]))
    for name, items in ordered:
        print(f"{name:<34}{len(items):>7}{len(items)*100/n:>13.1f}%")
    if dupes:
        print(f"{'duplicate stock numbers':<34}{len(dupes):>7}{len(dupes)*100/n:>13.1f}%")
    if not findings and not dupes:
        print("no issues found")

    print()
    for name, items in ordered:
        if not items:
            continue
        print(f"\n{name} — {len(items)} total, showing {min(args.show, len(items))}:")
        for it in items[:args.show]:
            print(f"    {it}")
    if dupes:
        print(f"\nduplicate stock numbers — {len(dupes)} total, showing {min(args.show, len(dupes))}:")
        for s, c in list(dupes.items())[:args.show]:
            print(f"    {s} × {c}")


if __name__ == "__main__":
    main()
