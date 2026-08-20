#!/usr/bin/env python3
"""Build out the A&B Motors storefront: collections, pages, policies, menus,
and the bulk draft -> active flip.

Everything here is idempotent. Run it as many times as you like; it updates
what exists and creates what doesn't. The one destructive action (replacing an
empty manual collection so it can become a smart one) is opt-in.

    python3 scripts/store_setup.py --check
    python3 scripts/store_setup.py --collections --pages --policies --menus
    python3 scripts/store_setup.py --vehicles --dry-run   # what the vehicle pages would do
    python3 scripts/store_setup.py --vehicles             # create/update them
    python3 scripts/store_setup.py --activate --dry-run
    python3 scripts/store_setup.py --activate            # the 9.4k flip, resumable

Credentials come from the CoreYard .env (SHOPIFY_STORE / SHOPIFY_ADMIN_TOKEN);
override with ABM_ENV=/path/to/.env. Nothing is printed that could leak a token.
"""
from __future__ import annotations

import argparse
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
CONTENT = REPO / "content"
STATE_PATH = REPO / ".store_setup_state.json"
API_VERSION = "2026-07"

DEFAULT_ENV = pathlib.Path(
    os.environ.get("ABM_ENV", REPO.parent / "coreyard" / ".env")
)

REQUIRED_SCOPES = {
    "collections": ["write_products"],
    "pages": ["write_online_store_pages"],
    "menus": ["write_online_store_navigation"],
    "policies": ["write_legal_policies"],
    "activate": ["write_products", "write_publications"],
}


# ─────────────────────────────────────────────────────────────── transport ──
class Shopify:
    def __init__(self, store: str, token: str):
        self.url = f"https://{store}/admin/api/{API_VERSION}/graphql.json"
        self.headers = {
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": token,
        }
        self._lock = threading.Lock()
        self._pause_until = 0.0

    def __call__(self, query: str, variables: dict | None = None, tries: int = 6) -> dict:
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        for attempt in range(tries):
            wait = self._pause_until - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            req = urllib.request.Request(self.url, data=body, headers=self.headers)
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    payload = json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError):
                if attempt < tries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise

            self._throttle(payload)
            errors = payload.get("errors") or []
            if errors:
                throttled = any(
                    (e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors
                )
                if throttled and attempt < tries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise RuntimeError(json.dumps(errors)[:500])
            return payload.get("data") or {}
        raise RuntimeError("exhausted retries")

    def _throttle(self, payload: dict) -> None:
        cost = ((payload.get("extensions") or {}).get("cost") or {}).get("throttleStatus")
        if not cost:
            return
        available = cost.get("currentlyAvailable", 1000)
        restore = cost.get("restoreRate", 100) or 100
        if available < 200:
            with self._lock:
                self._pause_until = max(
                    self._pause_until, time.monotonic() + (250 - available) / restore
                )


def load_env(path: pathlib.Path) -> dict:
    if not path.exists():
        sys.exit(f"no .env at {path} — set ABM_ENV to point at one")
    cfg = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    missing = [k for k in ("SHOPIFY_STORE", "SHOPIFY_ADMIN_TOKEN") if not cfg.get(k)]
    if missing:
        sys.exit(f"{path} is missing {', '.join(missing)}")
    return cfg


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"activated": [], "published": []}


_state_lock = threading.Lock()


def save_state(state: dict) -> None:
    with _state_lock:
        STATE_PATH.write_text(json.dumps(state))


def read_html(rel: str) -> str:
    return (CONTENT / rel).read_text(encoding="utf-8").strip()


# ────────────────────────────────────────────────────────────────── checks ──
def granted_scopes(gql: Shopify) -> set[str]:
    d = gql("{ currentAppInstallation { accessScopes { handle } } }")
    return {
        s["handle"] for s in ((d.get("currentAppInstallation") or {}).get("accessScopes") or [])
    }


def require(scopes: set[str], task: str) -> bool:
    need = [s for s in REQUIRED_SCOPES[task] if s not in scopes]
    if need:
        print(f"  ! skipping {task}: token is missing {', '.join(need)}")
        print("    re-run OAuth with the expanded SHOPIFY_SCOPES (see docs/LAUNCH.md)")
        return False
    return True


def cmd_check(gql: Shopify) -> None:
    scopes = granted_scopes(gql)
    print("granted scopes:", ", ".join(sorted(scopes)) or "(none)")
    for task, need in REQUIRED_SCOPES.items():
        ok = all(s in scopes for s in need)
        print(f"  {'OK ' if ok else 'NO '} {task:<12} needs {', '.join(need)}")

    d = gql("{ shop { name primaryDomain { host } } }")
    print(f"\nshop: {d['shop']['name']} @ {d['shop']['primaryDomain']['host']}")

    for status in ("ACTIVE", "DRAFT"):
        n = gql("query($q:String!){ productsCount(query:$q){count} }", {"q": f"status:{status}"})
        print(f"  products {status}: {n['productsCount']['count']}")

    d = gql("{ collections(first:50){ nodes{ handle productsCount{count} ruleSet{ appliedDisjunctively } } } }")
    for c in d["collections"]["nodes"]:
        kind = "smart" if c.get("ruleSet") else "manual"
        print(f"  collection {c['handle']:<26} {kind:<7} {c['productsCount']['count']:>6} products")

    if "read_publications" in scopes:
        d = gql("{ publications(first:20){ nodes{ id name } } }")
        for p in d["publications"]["nodes"]:
            print(f"  publication: {p['name']}")


# ───────────────────────────────────────────────────────────── collections ──
COLLECTION_FIELDS = "id handle title productsCount{count} ruleSet{ appliedDisjunctively rules{ column relation condition } }"


def find_collection(gql: Shopify, handle: str) -> dict | None:
    d = gql(
        "query($h:String!){ collectionByIdentifier(identifier:{handle:$h}){ %s } }" % COLLECTION_FIELDS,
        {"h": handle},
    )
    return d.get("collectionByIdentifier")


def collection_input(spec: dict) -> dict:
    if spec.get("in_stock_only"):
        rules = [{"column": "VARIANT_INVENTORY", "relation": "GREATER_THAN", "condition": "0"}]
    elif spec.get("tag"):
        # Vehicle collections. CoreYard already writes "Ford" and "Ford Ranger" as tags,
        # so an exact TAG match is the whole rule — no catalog change needed. EQUALS, not
        # CONTAINS: "Ford" CONTAINS would also sweep in every Ford Ranger and Ford Fusion,
        # and the make collection is supposed to be the union, not a duplicate of it.
        rules = [{"column": "TAG", "relation": "EQUALS", "condition": spec["tag"]}]
    else:
        # Shopify calls the product-type column TYPE, not PRODUCT_TYPE.
        rules = [
            {"column": "TYPE", "relation": "CONTAINS", "condition": term}
            for term in spec["contains"]
        ]
    payload = {
        "handle": spec["handle"],
        "title": spec["title"],
        "descriptionHtml": f"<p>{spec['description']}</p>",
        "sortOrder": spec.get("sort", "CREATED_DESC"),
        "ruleSet": {"appliedDisjunctively": True, "rules": rules},
    }
    # Without this the body copy becomes the meta description, which is far past the
    # ~160 characters Google will show. Set it explicitly where the spec says so.
    if spec.get("seo_title") or spec.get("seo_description"):
        payload["seo"] = {
            "title": spec.get("seo_title", spec["title"]),
            "description": spec.get("seo_description", ""),
        }
    return payload


def cmd_collections(gql: Shopify, specs: list[dict], replace_empty: bool, dry_run: bool = False) -> None:
    for spec in specs:
        existing = find_collection(gql, spec["handle"])
        payload = collection_input(spec)

        if dry_run:
            rules = payload["ruleSet"]["rules"]
            verb = "would update" if existing else "would create"
            if existing and not existing.get("ruleSet"):
                verb = "manual collection in the way — would skip"
            rule_text = ", ".join(f"{r['column']} {r['relation']} {r['condition']!r}" for r in rules[:3])
            if len(rules) > 3:
                rule_text += f", +{len(rules) - 3} more"
            print(f"    {spec['handle']:<34} {verb:<14} {rule_text}")
            continue

        if existing and not existing.get("ruleSet"):
            count = existing["productsCount"]["count"]
            if count > 0:
                print(f"  ! {spec['handle']}: manual collection with {count} products — left alone")
                continue
            if not replace_empty:
                print(f"  ! {spec['handle']}: empty manual collection; pass --replace-empty to convert")
                continue
            gql(
                "mutation($id:ID!){ collectionDelete(input:{id:$id}){ deletedCollectionId userErrors{message} } }",
                {"id": existing["id"]},
            )
            print(f"    {spec['handle']}: deleted empty manual collection")
            existing = None

        if existing:
            payload["id"] = existing["id"]
            d = gql(
                "mutation($input:CollectionInput!){ collectionUpdate(input:$input){ collection{ handle productsCount{count} } userErrors{ field message } } }",
                {"input": payload},
            )
            res = d["collectionUpdate"]
            verb = "updated"
        else:
            d = gql(
                "mutation($input:CollectionInput!){ collectionCreate(input:$input){ collection{ handle productsCount{count} } userErrors{ field message } } }",
                {"input": payload},
            )
            res = d["collectionCreate"]
            verb = "created"

        if res["userErrors"]:
            print(f"  ! {spec['handle']}: {res['userErrors']}")
        else:
            n = res["collection"]["productsCount"]["count"]
            print(f"    {spec['handle']:<26} {verb}, matches {n} products (still indexing if 0)")


# ─────────────────────────────────────────────────────────────────── pages ──
def cmd_pages(gql: Shopify, site: dict) -> None:
    for spec in site["pages"]:
        body = read_html(spec["file"])
        # There is no pageByHandle on QueryRoot; filter the connection instead.
        d = gql(
            "query($q:String!){ pages(first:1, query:$q){ nodes{ id handle } } }",
            {"q": f"handle:{spec['handle']}"},
        )
        nodes = [n for n in d["pages"]["nodes"] if n["handle"] == spec["handle"]]
        existing = nodes[0] if nodes else None
        fields = {
            "title": spec["title"],
            "handle": spec["handle"],
            "body": body,
            "isPublished": True,
        }
        if spec.get("template_suffix"):
            fields["templateSuffix"] = spec["template_suffix"]
        # Without an explicit description Shopify derives one from the body text and does
        # NOT decode entities on the way, so a page whose copy opens "A&amp;B Motors" ships
        # a meta description containing the literal "&amp;" — which the theme then escapes
        # again, and Google prints "A&amp;B Motors". /pages/about and /pages/warranty-returns
        # both did exactly that. Setting the description explicitly is also just better SEO
        # than a truncated first paragraph.
        if spec.get("seo_description") or spec.get("seo_title"):
            fields["seo"] = {
                "title": spec.get("seo_title", spec["title"]),
                "description": spec.get("seo_description", ""),
            }

        if existing:
            d = gql(
                "mutation($id:ID!,$page:PageUpdateInput!){ pageUpdate(id:$id,page:$page){ page{handle} userErrors{ field message } } }",
                {"id": existing["id"], "page": fields},
            )
            res, verb = d["pageUpdate"], "updated"
        else:
            d = gql(
                "mutation($page:PageCreateInput!){ pageCreate(page:$page){ page{handle} userErrors{ field message } } }",
                {"page": fields},
            )
            res, verb = d["pageCreate"], "created"

        print(
            f"  ! {spec['handle']}: {res['userErrors']}"
            if res["userErrors"]
            else f"    /pages/{spec['handle']:<22} {verb} ({len(body)} chars)"
        )


# ──────────────────────────────────────────────────────────────── policies ──
def cmd_policies(gql: Shopify, site: dict) -> None:
    for spec in site["policies"]:
        body = read_html(spec["file"])
        # ShopPolicyInput is keyed by policy type, not by id.
        d = gql(
            "mutation($p:ShopPolicyInput!){ shopPolicyUpdate(shopPolicy:$p){ shopPolicy{ type } userErrors{ field message } } }",
            {"p": {"type": spec["type"], "body": body}},
        )
        res = d["shopPolicyUpdate"]
        print(
            f"  ! {spec['type']}: {res['userErrors']}"
            if res["userErrors"]
            else f"    {spec['type']:<22} set ({len(body)} chars)"
        )


# ─────────────────────────────────────────────────────────────────── menus ──
def menu_items(items: list[dict], base: str) -> list[dict]:
    out = []
    for item in items:
        node = {"title": item["title"], "type": "HTTP", "url": base + item["url"]}
        if item.get("items"):
            node["items"] = menu_items(item["items"], base)
        out.append(node)
    return out


def cmd_menus(gql: Shopify, site: dict) -> None:
    d = gql("{ shop { primaryDomain { url } } }")
    base = d["shop"]["primaryDomain"]["url"].rstrip("/")

    d = gql("{ menus(first:50){ nodes{ id handle } } }")
    existing = {m["handle"]: m["id"] for m in d["menus"]["nodes"]}

    for spec in site["menus"]:
        items = menu_items(spec["items"], base)
        if spec["handle"] in existing:
            d = gql(
                "mutation($id:ID!,$title:String!,$handle:String!,$items:[MenuItemUpdateInput!]!){"
                " menuUpdate(id:$id,title:$title,handle:$handle,items:$items){ menu{handle} userErrors{ field message } } }",
                {"id": existing[spec["handle"]], "title": spec["title"], "handle": spec["handle"], "items": items},
            )
            res, verb = d["menuUpdate"], "updated"
        else:
            d = gql(
                "mutation($title:String!,$handle:String!,$items:[MenuItemCreateInput!]!){"
                " menuCreate(title:$title,handle:$handle,items:$items){ menu{handle} userErrors{ field message } } }",
                {"title": spec["title"], "handle": spec["handle"], "items": items},
            )
            res, verb = d["menuCreate"], "created"

        print(
            f"  ! {spec['handle']}: {res['userErrors']}"
            if res["userErrors"]
            else f"    menu {spec['handle']:<20} {verb}, {len(items)} top-level items"
        )


# ──────────────────────────────────────────────────────────────── activate ──
def online_store_publication(gql: Shopify) -> str | None:
    d = gql("{ publications(first:20){ nodes{ id name } } }")
    for p in d["publications"]["nodes"]:
        if p["name"] == "Online Store":
            return p["id"]
    return None


def draft_products(gql: Shopify, limit: int | None):
    """Yield draft products that have at least one photo."""
    query = "query($cursor:String){ products(first:250, after:$cursor, query:\"status:draft\"){ pageInfo{ hasNextPage endCursor } nodes{ id mediaCount{count} } } }"
    cursor, seen = None, 0
    while True:
        conn = gql(query, {"cursor": cursor})["products"]
        for p in conn["nodes"]:
            if (p.get("mediaCount") or {}).get("count", 0) < 1:
                continue
            yield p["id"]
            seen += 1
            if limit and seen >= limit:
                return
        if not conn["pageInfo"]["hasNextPage"]:
            return
        cursor = conn["pageInfo"]["endCursor"]


ACTIVATE = """mutation($id:ID!){
  productUpdate(product:{id:$id, status:ACTIVE}){ product{ id } userErrors{ field message } }
}"""

PUBLISH = """mutation($id:ID!,$pub:ID!){
  publishablePublish(id:$id, input:{publicationId:$pub}){ userErrors{ field message } }
}"""


def cmd_activate(gql: Shopify, limit: int | None, dry_run: bool, workers: int) -> None:
    pub = online_store_publication(gql)
    if not pub:
        print("  ! no Online Store publication found — is the sales channel enabled?")
        return

    state = load_state()
    done = set(state["activated"])
    ids = [pid for pid in draft_products(gql, limit) if pid not in done]
    print(f"    {len(ids)} photographed drafts to activate ({len(done)} already done)")
    if dry_run:
        print("    dry run — nothing changed")
        return
    if not ids:
        return

    counter = {"ok": 0, "err": 0}
    started = time.monotonic()

    def work(pid: str) -> None:
        try:
            res = gql(ACTIVATE, {"id": pid})["productUpdate"]
            if res["userErrors"]:
                raise RuntimeError(res["userErrors"])
            res = gql(PUBLISH, {"id": pid, "pub": pub})["publishablePublish"]
            if res["userErrors"]:
                raise RuntimeError(res["userErrors"])
        except Exception as exc:  # keep going; a bad product shouldn't stop 9k
            counter["err"] += 1
            if counter["err"] <= 5:
                print(f"  ! {pid}: {exc}")
            return
        counter["ok"] += 1
        with _state_lock:
            state["activated"].append(pid)
        if counter["ok"] % 100 == 0:
            rate = counter["ok"] / max(time.monotonic() - started, 1)
            left = (len(ids) - counter["ok"]) / max(rate, 0.1) / 60
            print(f"    {counter['ok']}/{len(ids)} active  ({rate:.1f}/s, ~{left:.0f} min left)")
            save_state(state)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, ids))

    save_state(state)
    print(f"    done: {counter['ok']} activated, {counter['err']} failed")


# ──────────────────────────────────────────────────────────────────── main ──
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="print scopes and current store state")
    ap.add_argument("--collections", action="store_true")
    ap.add_argument("--vehicles", action="store_true", help="the make/model vehicle collections in site.json")
    ap.add_argument("--pages", action="store_true")
    ap.add_argument("--policies", action="store_true")
    ap.add_argument("--menus", action="store_true")
    ap.add_argument("--activate", action="store_true", help="flip photographed drafts to ACTIVE and publish")
    ap.add_argument("--all", action="store_true", help="everything except --activate")
    ap.add_argument("--replace-empty", action="store_true", help="delete empty manual collections so they can be rebuilt as smart ones")
    ap.add_argument("--limit", type=int, help="cap how many products --activate touches")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    if not any([args.check, args.collections, args.vehicles, args.pages, args.policies, args.menus, args.activate, args.all]):
        ap.print_help()
        return

    cfg = load_env(DEFAULT_ENV)
    gql = Shopify(cfg["SHOPIFY_STORE"], cfg["SHOPIFY_ADMIN_TOKEN"])
    site = json.loads((CONTENT / "site.json").read_text())

    if args.check:
        cmd_check(gql)
        return

    scopes = granted_scopes(gql)

    if args.collections or args.all:
        print("collections:" + (" (dry run, nothing written)" if args.dry_run else ""))
        if require(scopes, "collections"):
            cmd_collections(gql, site["collections"], args.replace_empty, args.dry_run)
    if args.vehicles:
        specs = site.get("vehicle_collections") or []
        print(f"vehicle collections: {len(specs)}" + (" (dry run, nothing written)" if args.dry_run else ""))
        if not specs:
            print("  ! none defined in site.json")
        elif require(scopes, "collections"):
            cmd_collections(gql, specs, args.replace_empty, args.dry_run)
    if args.pages or args.all:
        print("pages:")
        if require(scopes, "pages"):
            cmd_pages(gql, site)
    if args.policies or args.all:
        print("policies:")
        if require(scopes, "policies"):
            cmd_policies(gql, site)
    if args.menus or args.all:
        print("menus:")
        if require(scopes, "menus"):
            cmd_menus(gql, site)
    if args.activate:
        print("activate:")
        if require(scopes, "activate"):
            cmd_activate(gql, args.limit, args.dry_run, args.workers)


if __name__ == "__main__":
    main()
