#!/usr/bin/env python3
"""Bring live product titles back in line with what CoreYard would generate.

Most of the catalogue was titled by an earlier generation of CoreYard's SEO builder and
has drifted from what the current one produces: dashed year spans, " / " between models
and "& N more", none of which `build_title()` will emit today. The sync cannot correct
this on its own — its diff compares the yard against its own state file, never against
what Shopify is actually showing, so a stale title looks like no change at all.

This job closes that gap directly: ask the yard for every part, ask CoreYard what each
one *should* be called, ask Shopify what it *is* called, and rewrite the mismatches.

Titles come from CoreYard, so this needs its virtualenv (the yard DB is reached over SMB):

    cd ../coreyard && .venv/bin/python ../abmotorsla/scripts/retitle_from_coreyard.py --plan
    cd ../coreyard && .venv/bin/python ../abmotorsla/scripts/retitle_from_coreyard.py --apply

`--plan` writes nothing. Progress is checkpointed against the exact title applied, so an
interrupted run resumes, and a title that changes again is never skipped as "already done".
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
COREYARD = pathlib.Path(os.environ.get("ABM_COREYARD", REPO.parent / "coreyard"))
ENV = pathlib.Path(os.environ.get("ABM_ENV", COREYARD / ".env"))
STATE = REPO / ".retitle_state.json"
API_VERSION = "2026-07"


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
            throttled = any(
                (e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors
            )
            if throttled and attempt < tries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(json.dumps(errors)[:400])
        return payload.get("data") or {}
    raise RuntimeError("exhausted retries")


SCAN = """query($cursor:String){
  products(first:100, after:$cursor, query:"status:active"){
    pageInfo{ hasNextPage endCursor }
    nodes{ id handle title variants(first:1){ nodes{ sku } } }
  }
}"""

RENAME = """mutation($id:ID!,$title:String!){
  productUpdate(product:{id:$id, title:$title}){ userErrors{ field message } }
}"""


def coreyard_titles(wanted: set[str]) -> dict[str, str]:
    """R# -> the title CoreYard's current builder would publish, for the given parts.

    Interchange fitment has to be attached before rendering. `fetch_parts` returns the
    donor vehicle only, and `build_title` falls back to it when `part.fitment` is empty —
    so titling straight off a bare fetch silently replaces every multi-model title with
    the single donor car. `run_sync` resolves fitment for the same reason before it
    publishes; only the parts we are about to compare are resolved, to keep it cheap.
    """
    sys.path.insert(0, str(COREYARD))
    try:
        from coreyard.transform.seo import build_title
        from coreyard.yms.db import connect
        from coreyard.yms.interchange import InterchangeResolver
        from coreyard.yms.inventory import fetch_parts
    except ImportError as exc:                      # impacket etc. live in CoreYard's venv
        sys.exit(f"cannot import CoreYard ({exc}).\n"
                 f"Run this with CoreYard's interpreter: {COREYARD}/.venv/bin/python")

    parts = [p for p in fetch_parts(limit=None) if str(p.uid()) in wanted]
    print(f"  resolving interchange fitment for {len(parts)} part(s) ...")
    with connect() as conn:
        InterchangeResolver(conn).attach(parts)
    return {str(p.uid()): build_title(p) for p in parts}


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true", help="report what would change, write nothing")
    ap.add_argument("--apply", action="store_true", help="rewrite the mismatched titles")
    ap.add_argument("--show", type=int, default=15, help="sample rewrites to print (default 15)")
    ap.add_argument("--dump", default=None, help="write the full before/after diff to this JSON file")
    ap.add_argument("--limit", type=int, default=None, help="stop after N rewrites (for a cautious first run)")
    args = ap.parse_args()
    if not (args.plan or args.apply):
        ap.print_help()
        return

    cfg = load_env()
    url = f"https://{cfg['SHOPIFY_STORE']}/admin/api/{API_VERSION}/graphql.json"
    headers = {"Content-Type": "application/json",
               "X-Shopify-Access-Token": cfg["SHOPIFY_ADMIN_TOKEN"]}

    print("reading live product titles ...")
    cursor, published, no_sku = None, [], 0
    while True:
        page = gql(url, headers, SCAN, {"cursor": cursor})["products"]
        for p in page["nodes"]:
            variants = p["variants"]["nodes"]
            sku = (variants[0].get("sku") or "").strip() if variants else ""
            if not sku:
                no_sku += 1
                continue
            published.append((p["id"], sku, p["title"]))
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    print(f"  {len(published)} live products ({no_sku} without a SKU)")

    print("asking the yard what each one should be called ...")
    want = coreyard_titles({sku for _, sku, _ in published})

    live = [(pid, sku, title, want[sku]) for pid, sku, title in published if sku in want]
    unknown = len(published) - len(live)
    stale = [row for row in live if row[2] != row[3]]
    print(f"  {len(live)} live products matched to a yard part"
          f"  ({unknown} not in the yard's current list)")
    print(f"  {len(stale)} titles differ from what CoreYard would publish")

    if not stale:
        print("nothing to do — every live title already matches.")
        return

    # Safety read-out. A rewrite that drops vehicles off a title is a downgrade even when
    # the wording improves, so count those before anyone runs --apply.
    def models_in(t: str) -> int:
        extra = re.search(r"and (\d+) more", t)
        return t.count(",") + 1 + (int(extra.group(1)) if extra else 0)

    lost = [r for r in stale if models_in(r[3]) < models_in(r[2])]
    gained = [r for r in stale if models_in(r[3]) > models_in(r[2])]
    shorter = [r for r in stale if len(r[3]) < len(r[2]) * 0.6]
    print(f"\n  fitment lost:    {len(lost)}   (a title that now covers fewer vehicles)")
    print(f"  fitment gained:  {len(gained)}")
    print(f"  much shorter:    {len(shorter)}   (under 60% of the old length)")

    if args.dump:
        pathlib.Path(args.dump).write_text(json.dumps(
            [{"sku": s, "now": o, "new": n} for _, s, o, n in stale], indent=1))
        print(f"  full diff written to {args.dump}")

    sample = stale if len(stale) <= args.show else [
        stale[i * len(stale) // args.show] for i in range(args.show)]
    for _, sku, old, new in sample:
        print(f"\n  R#{sku}\n    now:  {old[:120]}\n    new:  {new[:120]}")
    if len(stale) > args.show:
        print(f"\n  (sampled evenly across all {len(stale)})")

    if args.plan:
        print(f"\nplan only — nothing written. Re-run with --apply to rewrite {len(stale)} titles.")
        return

    # Resume on the title we are about to write, not just the product id: if a part is
    # re-titled again later, the recorded value no longer matches and it is retried.
    state = load_state()
    todo = [row for row in stale if state.get(row[0]) != row[3]]
    print(f"\napplying {len(todo)} rewrite(s) ({len(stale) - len(todo)} already done in an earlier run)")
    if args.limit:
        todo = todo[: args.limit]
        print(f"  limited to {len(todo)} this run")

    done = 0
    try:
        for pid, sku, _old, new in todo:
            res = gql(url, headers, RENAME, {"id": pid, "title": new})
            errs = ((res.get("productUpdate") or {}).get("userErrors")) or []
            if errs:
                print(f"  R#{sku}: {errs[0].get('message')}")
                continue
            state[pid] = new
            done += 1
            if done % 100 == 0:
                STATE.write_text(json.dumps(state))
                print(f"  {done}/{len(todo)}")
    finally:
        STATE.write_text(json.dumps(state))

    print(f"retitled {done} product(s). State in {STATE.name} — delete it to force a full re-run.")


if __name__ == "__main__":
    main()
