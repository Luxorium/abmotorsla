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
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from _shopify import REPO, Shopify

STATE = REPO / ".tag_shipping_state.json"

TAGS = {
    "PICKUP": "ship:pickup-only",
    "A": "ship:freight-299",
    "B": "ship:freight-199",
    "ground": "ship:free",
}
ALL_TAGS = set(TAGS.values())

_lock = threading.Lock()


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

    gql = Shopify.from_env()
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
