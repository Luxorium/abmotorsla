#!/usr/bin/env python3
"""Catalog cleanup: drop Shopify's sample products, strip the "– OEM" suffix
from titles, and fill in blank photo alt text.

"OEM" stays in seo.title and seo.description on purpose — it's a high-intent
search term for used parts, and those fields are what Google reads. Only the
customer-facing title is cleaned. Pass --strip-seo-oem to remove it there too.

    python3 scripts/cleanup_listings.py --plan
    python3 scripts/cleanup_listings.py --delete-samples
    python3 scripts/cleanup_listings.py --titles --alt
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

REPO = pathlib.Path(__file__).resolve().parent.parent
STATE = REPO / ".cleanup_state.json"
ENV = pathlib.Path(os.environ.get("ABM_ENV", REPO.parent / "coreyard" / ".env"))
API_VERSION = "2026-07"

# Matches " – OEM", " - OEM", " — OEM" and a bare trailing " OEM".
OEM_SUFFIX = re.compile(r"\s*[–—-]?\s*OEM\s*$", re.IGNORECASE)

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


SCAN = """query($cursor:String){
  products(first:100, after:$cursor){
    pageInfo{ hasNextPage endCursor }
    nodes{ id handle title seo{ title description }
      media(first:10){ nodes{ id alt } } }
  }
}"""

RENAME = """mutation($id:ID!,$title:String!){
  productUpdate(product:{id:$id, title:$title}){ userErrors{ field message } }
}"""

RENAME_SEO = """mutation($id:ID!,$title:String!,$seoTitle:String!,$seoDesc:String!){
  productUpdate(product:{id:$id, title:$title, seo:{title:$seoTitle, description:$seoDesc}}){
    userErrors{ field message }
  }
}"""

SET_ALT = """mutation($mid:ID!,$pid:ID!,$alt:String!){
  productUpdateMedia(productId:$pid, media:[{id:$mid, alt:$alt}]){ userErrors{ field message } }
}"""

DELETE = """mutation($id:ID!){
  productDelete(input:{id:$id}){ deletedProductId userErrors{ field message } }
}"""


def is_sample(p: dict) -> bool:
    return "asset-pack" in p["handle"] or p["title"].strip().lower() == "example product"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--delete-samples", action="store_true")
    ap.add_argument("--titles", action="store_true")
    ap.add_argument("--alt", action="store_true")
    ap.add_argument("--strip-seo-oem", action="store_true", help="also remove OEM from seo.title/description")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    if not any([args.plan, args.delete_samples, args.titles, args.alt]):
        ap.print_help()
        return

    cfg = load_env()
    gql = Shopify(cfg["SHOPIFY_STORE"], cfg["SHOPIFY_ADMIN_TOKEN"])

    print("scanning…", end="", flush=True)
    samples, retitle, altfix = [], [], []
    cursor = None
    while True:
        conn = gql(SCAN, {"cursor": cursor})["products"]
        for p in conn["nodes"]:
            if is_sample(p):
                samples.append((p["id"], p["title"], p["handle"]))
                continue
            new_title = OEM_SUFFIX.sub("", p["title"]).rstrip(" –—-")
            if new_title != p["title"] and len(new_title) > 8:
                retitle.append((p["id"], p["title"], new_title, p.get("seo") or {}))
            for m in (p.get("media") or {}).get("nodes", []):
                if not (m.get("alt") or "").strip():
                    altfix.append((p["id"], m["id"], new_title or p["title"]))
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]
    print(" done\n")

    print(f"sample products to delete : {len(samples)}")
    print(f"titles to strip OEM from  : {len(retitle)}")
    print(f"photos missing alt text   : {len(altfix)}")

    if args.plan:
        for _, old, new, _ in retitle[:5]:
            print(f"\n  before: {old}\n  after : {new}")
        for _, t, h in samples:
            print(f"\n  delete: {t} [{h}]")
        return

    if args.delete_samples and samples:
        print("\ndeleting sample products:")
        for pid, title, handle in samples:
            res = gql(DELETE, {"id": pid})["productDelete"]
            print(f"  {'ERR ' + str(res['userErrors']) if res['userErrors'] else 'deleted'}  {title} [{handle}]")

    if args.titles and retitle:
        print(f"\nrewriting {len(retitle)} titles:")
        done = set(json.loads(STATE.read_text())) if STATE.exists() else set()
        jobs = [j for j in retitle if j[0] not in done]
        counter = {"ok": 0, "err": 0}
        started = time.monotonic()

        def work(job):
            pid, _old, new, seo = job
            try:
                if args.strip_seo_oem:
                    st = OEM_SUFFIX.sub("", seo.get("title") or new).rstrip(" –—-")
                    sd = re.sub(r"\bUsed OEM\b", "Used", seo.get("description") or "", flags=re.IGNORECASE)
                    res = gql(RENAME_SEO, {"id": pid, "title": new, "seoTitle": st, "seoDesc": sd})
                else:
                    res = gql(RENAME, {"id": pid, "title": new})
                errs = res["productUpdate"]["userErrors"]
                if errs:
                    raise RuntimeError(errs)
            except Exception as exc:
                counter["err"] += 1
                if counter["err"] <= 5:
                    print(f"  ! {pid}: {exc}")
                return
            counter["ok"] += 1
            with _lock:
                done.add(pid)
            if counter["ok"] % 250 == 0:
                rate = counter["ok"] / max(time.monotonic() - started, 1)
                print(f"    {counter['ok']}/{len(jobs)}  ({rate:.1f}/s, ~{(len(jobs)-counter['ok'])/max(rate,.1)/60:.0f} min left)")
                STATE.write_text(json.dumps(sorted(done)))

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(work, jobs))
        STATE.write_text(json.dumps(sorted(done)))
        print(f"  titles: {counter['ok']} rewritten, {counter['err']} failed")

    if args.alt and altfix:
        print(f"\nsetting alt text on {len(altfix)} photos:")
        ok = err = 0
        for pid, mid, title in altfix:
            try:
                res = gql(SET_ALT, {"mid": mid, "pid": pid, "alt": title[:512]})["productUpdateMedia"]
                if res["userErrors"]:
                    raise RuntimeError(res["userErrors"])
                ok += 1
            except Exception as exc:
                err += 1
                print(f"  ! {mid}: {exc}")
        print(f"  alt text: {ok} set, {err} failed")


if __name__ == "__main__":
    main()
