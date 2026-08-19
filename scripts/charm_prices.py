#!/usr/bin/env python3
"""Round every price to the nearest .99.

Rule: for a price P, the candidates are floor(P) - 0.01 and floor(P) + 0.99;
whichever is closer wins, ties round up. So $50.00 -> $49.99 (a penny down),
$50.50 -> $50.99 (49c up), $8.00 -> $7.99. Prices already ending in .99 are
left alone. Nothing is ever priced below $0.99.

    python3 scripts/charm_prices.py --plan       # impact report, no writes
    python3 scripts/charm_prices.py --dry-run    # per-product preview
    python3 scripts/charm_prices.py              # apply (resumable)

IMPORTANT: the yard system is the source of truth for price. The next CoreYard
sync will overwrite anything changed here unless the same rounding is applied
in CoreYard's transform. Run this once for the existing catalog, then move the
rule upstream — otherwise prices drift back to round dollars.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

REPO = pathlib.Path(__file__).resolve().parent.parent
STATE = REPO / ".charm_prices_state.json"
API_VERSION = "2026-07"
ENV = pathlib.Path(os.environ.get("ABM_ENV", REPO.parent / "coreyard" / ".env"))
MIN_PRICE = 0.99

_lock = threading.Lock()


def charm(price: float) -> float:
    """Nearest .99 to `price`; ties go up."""
    if price <= MIN_PRICE:
        return MIN_PRICE
    base = math.floor(price)
    low, high = base - 0.01, base + 0.99
    if low < MIN_PRICE:
        return MIN_PRICE
    return low if (price - low) < (high - price) else high


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
        self._pause = 0.0

    def __call__(self, query: str, variables: dict | None = None, tries: int = 6) -> dict:
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        for attempt in range(tries):
            wait = self._pause - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                req = urllib.request.Request(self.url, data=body, headers=self.headers)
                with urllib.request.urlopen(req, timeout=90) as r:
                    payload = json.loads(r.read())
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
                if attempt < tries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
            cost = ((payload.get("extensions") or {}).get("cost") or {}).get("throttleStatus") or {}
            if cost.get("currentlyAvailable", 1000) < 200:
                with _lock:
                    self._pause = max(self._pause, time.monotonic() + 1.0)
            errors = payload.get("errors") or []
            if errors:
                if any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors) and attempt < tries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise RuntimeError(json.dumps(errors)[:400])
            return payload.get("data") or {}
        raise RuntimeError("exhausted retries")


PRODUCTS = """query($cursor:String){
  products(first:250, after:$cursor){
    pageInfo{ hasNextPage endCursor }
    nodes{ id title variants(first:1){ nodes{ id price } } }
  }
}"""

UPDATE = """mutation($pid:ID!,$vars:[ProductVariantsBulkInput!]!){
  productVariantsBulkUpdate(productId:$pid, variants:$vars){
    userErrors{ field message }
  }
}"""


def scan(gql: Shopify):
    cursor = None
    while True:
        conn = gql(PRODUCTS, {"cursor": cursor})["products"]
        for p in conn["nodes"]:
            vs = p["variants"]["nodes"]
            if vs:
                yield p["id"], p["title"], vs[0]["id"], float(vs[0]["price"])
        if not conn["pageInfo"]["hasNextPage"]:
            return
        cursor = conn["pageInfo"]["endCursor"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    cfg = load_env()
    gql = Shopify(cfg["SHOPIFY_STORE"], cfg["SHOPIFY_ADMIN_TOKEN"])

    print("scanning catalog…")
    rows = list(scan(gql))
    print(f"  {len(rows)} products\n")

    todo, up, down, delta = [], 0, 0, 0.0
    for pid, title, vid, price in rows:
        new = charm(price)
        if abs(new - price) < 0.0001:
            continue
        todo.append((pid, vid, price, new, title))
        delta += new - price
        if new > price:
            up += 1
        else:
            down += 1

    if args.plan or args.dry_run:
        already = len(rows) - len(todo)
        print(f"already ending in .99 : {already}")
        print(f"to change             : {len(todo)}  ({up} up, {down} down)")
        print(f"net catalog value     : {delta:+,.2f} across all parts")
        print(f"average change        : {delta/max(len(todo),1):+.3f} per changed part\n")
        for _, _, old, new, title in todo[:25]:
            print(f"  ${old:>9,.2f} -> ${new:>9,.2f}   {title[:58]}")
        print("  … nothing written" if args.dry_run else "  … plan only")
        return

    if args.limit:
        todo = todo[:args.limit]

    done = set(json.loads(STATE.read_text())) if STATE.exists() else set()
    todo = [t for t in todo if t[1] not in done]
    print(f"{len(todo)} prices to update")
    if not todo:
        return

    counter = {"ok": 0, "err": 0}
    started = time.monotonic()

    def work(job):
        pid, vid, _old, new, _title = job
        try:
            res = gql(UPDATE, {"pid": pid, "vars": [{"id": vid, "price": f"{new:.2f}"}]})["productVariantsBulkUpdate"]
            if res["userErrors"]:
                raise RuntimeError(res["userErrors"])
        except Exception as exc:
            counter["err"] += 1
            if counter["err"] <= 5:
                print(f"  ! {vid}: {exc}")
            return
        counter["ok"] += 1
        with _lock:
            done.add(vid)
        if counter["ok"] % 250 == 0:
            rate = counter["ok"] / max(time.monotonic() - started, 1)
            print(f"    {counter['ok']}/{len(todo)}  ({rate:.1f}/s, ~{(len(todo)-counter['ok'])/max(rate,.1)/60:.0f} min left)")
            STATE.write_text(json.dumps(sorted(done)))

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, todo))

    STATE.write_text(json.dumps(sorted(done)))
    print(f"done: {counter['ok']} prices updated, {counter['err']} failed")


if __name__ == "__main__":
    main()
