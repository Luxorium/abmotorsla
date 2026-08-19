#!/usr/bin/env python3
"""Tag every product with how it actually ships, so the storefront can say so.

The shipping profiles decide what checkout charges. Tags are what the *theme*
can see, and the theme has to warn a shopper before checkout — a customer who
adds a door to their cart should learn it is pickup-only on the product page,
not by hitting a dead checkout.

Tags applied (one per product):
    ship:pickup-only     body panels and glass, never shipped
    ship:freight-299     engines, transmissions, axles, K-frames
    ship:freight-199     transfer cases, differential carriers
    ship:free            everything else

    python3 scripts/tag_shipping.py --plan
    python3 scripts/tag_shipping.py --apply
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
ENV = pathlib.Path(os.environ.get("ABM_ENV", REPO.parent / "coreyard" / ".env"))
STATE = REPO / ".tag_shipping_state.json"
API_VERSION = "2026-07"

TAGS = {
    "PICKUP": "ship:pickup-only",
    "A": "ship:freight-299",
    "B": "ship:freight-199",
    "ground": "ship:free",
}
ALL_TAGS = set(TAGS.values())

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

    def __call__(self, query, variables=None, tries=6):
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
                    time.sleep(2 * (attempt + 1))
                    continue
                raise RuntimeError(json.dumps(errors)[:400])
            return payload.get("data") or {}
        raise RuntimeError("exhausted retries")


SCAN = """
query($cursor: String) {
  products(first: 250, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes { id productType tags }
  }
}
"""

ADD = """
mutation($id: ID!, $tags: [String!]!) {
  tagsAdd(id: $id, tags: $tags) { userErrors { field message } }
}
"""

REMOVE = """
mutation($id: ID!, $tags: [String!]!) {
  tagsRemove(id: $id, tags: $tags) { userErrors { field message } }
}
"""


def classify(freight: dict, product_type: str) -> str:
    t = (product_type or "").lower()
    for group in ("PICKUP", "A", "B"):
        for pattern in freight[group]:
            if pattern in t:
                return group
    return "ground"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    if not (args.plan or args.apply):
        ap.print_help()
        return

    cfg = load_env()
    gql = Shopify(cfg["SHOPIFY_STORE"], cfg["SHOPIFY_ADMIN_TOKEN"])
    freight = json.loads((REPO / "content" / "freight.json").read_text())

    print("scanning…")
    jobs, counts = [], collections.Counter()
    cursor = None
    while True:
        conn = gql(SCAN, {"cursor": cursor})["products"]
        for p in conn["nodes"]:
            want = TAGS[classify(freight, p["productType"])]
            counts[want] += 1
            have = set(p["tags"]) & ALL_TAGS
            if have == {want}:
                continue
            jobs.append((p["id"], want, sorted(have - {want})))
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]

    print(f"  target tags: " + ", ".join(f"{t}={c}" for t, c in counts.most_common()))
    print(f"  products needing a change: {len(jobs)}")
    if args.plan:
        for pid, want, stale in jobs[:8]:
            print(f"    +{want}" + (f"  -{stale}" if stale else ""))
        print("  plan only")
        return
    if not jobs:
        return

    done = set(json.loads(STATE.read_text())) if STATE.exists() else set()
    jobs = [j for j in jobs if j[0] not in done]
    counter = {"ok": 0, "err": 0}
    started = time.monotonic()

    def work(job):
        pid, want, stale = job
        try:
            res = gql(ADD, {"id": pid, "tags": [want]})["tagsAdd"]
            if res["userErrors"]:
                raise RuntimeError(res["userErrors"])
            if stale:
                res = gql(REMOVE, {"id": pid, "tags": stale})["tagsRemove"]
                if res["userErrors"]:
                    raise RuntimeError(res["userErrors"])
        except Exception as exc:
            counter["err"] += 1
            if counter["err"] <= 5:
                print(f"  ! {pid}: {exc}")
            return
        counter["ok"] += 1
        with _lock:
            done.add(pid)
        if counter["ok"] % 500 == 0:
            rate = counter["ok"] / max(time.monotonic() - started, 1)
            print(f"    {counter['ok']}/{len(jobs)}  ({rate:.1f}/s, ~{(len(jobs)-counter['ok'])/max(rate,.1)/60:.0f} min left)")
            STATE.write_text(json.dumps(sorted(done)))

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, jobs))
    STATE.write_text(json.dumps(sorted(done)))
    print(f"done: {counter['ok']} tagged, {counter['err']} failed")


if __name__ == "__main__":
    main()
