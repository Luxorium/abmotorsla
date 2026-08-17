#!/usr/bin/env python3
"""Build theme/assets/vehicles.json — the year/make/model index for the picker.

CoreYard tags every part with "<year> <make> <model>" (plus bare make and
"make model"), so the catalog already knows which vehicles we have parts for.
This distills that into make -> model -> [years] and writes it as a theme asset,
so the picker is instant and needs no API calls from the storefront.

Re-run it after a big sync; it is read-only against Shopify.

    python3 scripts/build_vehicles.py
"""
from __future__ import annotations

import collections
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = REPO / "theme" / "assets" / "vehicles.json"
ENV = pathlib.Path(os.environ.get("ABM_ENV", REPO.parent / "abmotors-to-shopify" / ".env"))
API_VERSION = "2025-07"

# "2012 Honda Civic" -> year 2012, rest "Honda Civic"
YEAR_TAG = re.compile(r"^(19[5-9]\d|20[0-4]\d)\s+(.+)$")

# Makes as CoreYard writes them. A tag's first word is the make except for these
# two-word ones, where the model would otherwise swallow part of the make.
TWO_WORD_MAKES = {
    "land rover", "alfa romeo", "aston martin", "mercedes benz", "mercedes-benz",
    "rolls royce", "am general", "general motors",
}


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


def gql(url, headers, query, variables=None, tries=8):
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
            if any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors) and attempt < tries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(json.dumps(errors)[:400])
        return payload.get("data") or {}
    raise RuntimeError("exhausted retries")


SCAN = """
query($cursor: String) {
  products(first: 250, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes { tags }
  }
}
"""


def split_make_model(rest: str) -> tuple[str, str] | None:
    low = rest.lower()
    for mk in TWO_WORD_MAKES:
        if low.startswith(mk + " "):
            return rest[: len(mk)], rest[len(mk) + 1:]
    parts = rest.split(" ", 1)
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def main() -> None:
    cfg = load_env()
    url = f"https://{cfg['SHOPIFY_STORE']}/admin/api/{API_VERSION}/graphql.json"
    headers = {"Content-Type": "application/json", "X-Shopify-Access-Token": cfg["SHOPIFY_ADMIN_TOKEN"]}

    tree: dict[str, dict[str, set]] = collections.defaultdict(lambda: collections.defaultdict(set))
    parts_per_make = collections.Counter()
    cursor, n = None, 0

    print("scanning tags…", end="", flush=True)
    while True:
        conn = gql(url, headers, SCAN, {"cursor": cursor})["products"]
        for p in conn["nodes"]:
            n += 1
            makes_here = set()
            for tag in p["tags"]:
                m = YEAR_TAG.match(tag.strip())
                if not m:
                    continue
                year, rest = int(m.group(1)), m.group(2).strip()
                split = split_make_model(rest)
                if not split:
                    continue
                make, model = split[0].strip(), split[1].strip()
                if len(make) < 2 or len(model) < 1:
                    continue
                tree[make][model].add(year)
                makes_here.add(make)
            for mk in makes_here:
                parts_per_make[mk] += 1
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]
        if n % 2000 < 250:
            print(".", end="", flush=True)
    print(" done")

    out = {}
    for make, models in tree.items():
        # Drop one-off noise: a make needs a couple of parts to be worth listing.
        if parts_per_make[make] < 2:
            continue
        out[make] = {model: sorted(years) for model, years in sorted(models.items())}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"makes": dict(sorted(out.items()))}, separators=(",", ":")))

    models = sum(len(m) for m in out.values())
    print(f"{n} products scanned")
    print(f"{len(out)} makes, {models} models -> {OUT.relative_to(REPO)} ({OUT.stat().st_size / 1024:.0f} KB)")
    print("\ntop makes by parts:")
    for mk, c in parts_per_make.most_common(12):
        if mk in out:
            print(f"  {mk:<20}{c:>5} parts, {len(out[mk]):>3} models")


if __name__ == "__main__":
    main()
