#!/usr/bin/env python3
"""Set a shipping weight on every product, keyed by part type.

None of the 9,407 synced products carry a weight, which makes weight-based and
carrier-calculated shipping rates impossible — everything prices as 0 lb. This
applies the packed-weight table in content/weights.json.

    python3 scripts/backfill_weights.py --plan          # coverage report, no writes
    python3 scripts/backfill_weights.py --dry-run       # per-product preview
    python3 scripts/backfill_weights.py                 # apply (resumable)

Weights are estimates good enough for rate calculation, not certified scale
readings. Verify the freight classes (engines, transmissions, boxes) against a
real scale before trusting them on an LTL bill.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

REPO = pathlib.Path(__file__).resolve().parent.parent
TABLE = REPO / "content" / "weights.json"
STATE = REPO / ".weights_state.json"
API_VERSION = "2025-07"
ENV = pathlib.Path(os.environ.get("ABM_ENV", REPO.parent / "abmotors-to-shopify" / ".env"))

_lock = threading.Lock()


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


class WeightTable:
    def __init__(self, path: pathlib.Path):
        data = json.loads(path.read_text())
        self.unit = data["unit"]
        self.default = data["default"]
        # Deliberately biased heavy: a carrier that reweighs an under-declared
        # shipment rebills you, which costs more than the extra postage would.
        self.safety = data.get("safety_multiplier", 1.0)
        # Order matters: first substring hit wins, so specific beats general.
        self.rules = [(p.lower(), w) for p, w in data["rules"]]

    def lookup(self, product_type: str) -> tuple[float, str]:
        t = (product_type or "").lower()
        for pattern, weight in self.rules:
            if pattern in t:
                return self._pad(weight), pattern
        return self._pad(self.default), "(default)"

    def _pad(self, weight: float) -> float:
        import math as _math
        return float(_math.ceil(weight * self.safety))


PRODUCTS = """query($cursor:String){
  products(first:250, after:$cursor){
    pageInfo{ hasNextPage endCursor }
    nodes{ id productType
      variants(first:1){ nodes{ id
        inventoryItem{ id measurement{ weight{ value unit } } } } } }
  }
}"""

SET_WEIGHT = """mutation($id:ID!,$w:Float!,$u:WeightUnit!){
  inventoryItemUpdate(id:$id, input:{ measurement:{ weight:{ value:$w, unit:$u } } }){
    inventoryItem{ id } userErrors{ field message }
  }
}"""


def scan(gql: Shopify):
    cursor = None
    while True:
        conn = gql(PRODUCTS, {"cursor": cursor})["products"]
        for p in conn["nodes"]:
            vs = p["variants"]["nodes"]
            if not vs:
                continue
            item = vs[0]["inventoryItem"] or {}
            existing = ((item.get("measurement") or {}).get("weight") or {}).get("value")
            yield p["productType"] or "", item.get("id"), existing
        if not conn["pageInfo"]["hasNextPage"]:
            return
        cursor = conn["pageInfo"]["endCursor"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true", help="coverage report by part type, no writes")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--force", action="store_true", help="overwrite weights that are already set")
    args = ap.parse_args()

    cfg = load_env()
    gql = Shopify(cfg["SHOPIFY_STORE"], cfg["SHOPIFY_ADMIN_TOKEN"])
    table = WeightTable(TABLE)

    print("scanning catalog…")
    rows = list(scan(gql))
    print(f"  {len(rows)} variants\n")

    matched = collections.Counter()
    fell_through = collections.Counter()
    todo = []
    for ptype, item_id, existing in rows:
        weight, rule = table.lookup(ptype)
        if rule == "(default)":
            fell_through[ptype] += 1
        else:
            matched[rule] += 1
        if item_id and (args.force or not existing):
            todo.append((item_id, weight, ptype))

    if args.plan:
        print(f"{'rule matched':<34}{'products':>9}")
        for rule, n in matched.most_common(40):
            print(f"  {rule[:32]:<32}{n:>9}")
        total_default = sum(fell_through.values())
        print(f"\nfell through to default ({table.default} lb): {total_default} products "
              f"across {len(fell_through)} types")
        for ptype, n in fell_through.most_common(25):
            print(f"  {n:>5}  {ptype}")
        print(f"\nwould write {len(todo)} weights")
        return

    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} products need a weight")
    if args.dry_run:
        for item_id, weight, ptype in todo[:40]:
            print(f"  {weight:>6.0f} lb  {ptype}")
        print("  … dry run, nothing written")
        return
    if not todo:
        return

    done = set()
    if STATE.exists():
        done = set(json.loads(STATE.read_text()))
    todo = [t for t in todo if t[0] not in done]

    counter = {"ok": 0, "err": 0}
    started = time.monotonic()

    def work(job):
        item_id, weight, _ = job
        try:
            res = gql(SET_WEIGHT, {"id": item_id, "w": float(weight), "u": table.unit})["inventoryItemUpdate"]
            if res["userErrors"]:
                raise RuntimeError(res["userErrors"])
        except Exception as exc:
            counter["err"] += 1
            if counter["err"] <= 5:
                print(f"  ! {item_id}: {exc}")
            return
        counter["ok"] += 1
        with _lock:
            done.add(item_id)
        if counter["ok"] % 250 == 0:
            rate = counter["ok"] / max(time.monotonic() - started, 1)
            print(f"    {counter['ok']}/{len(todo)}  ({rate:.1f}/s, ~{(len(todo)-counter['ok'])/max(rate,.1)/60:.0f} min left)")
            STATE.write_text(json.dumps(sorted(done)))

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, todo))

    STATE.write_text(json.dumps(sorted(done)))
    print(f"done: {counter['ok']} weights set, {counter['err']} failed")


if __name__ == "__main__":
    main()
