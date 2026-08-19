#!/usr/bin/env python3
"""Pull new Shopify orders and hand them to CoreYard's pull-ticket printer.

CoreYard already knows how to turn an order into a printable pull ticket (bin
location from the yard database, buyer and address from Shopify) — see
`coreyard/webhook.py`. That path expects Shopify to POST to a public URL, and the
yard machine sits behind NAT with no inbound port.

Polling avoids that entirely: a cron job asks Shopify what's new, writes each
order to JSON, and replays it through the same ticket renderer. Slower than a
webhook by up to the poll interval, which for a counter that opens at 8am is
irrelevant.

    python3 scripts/poll_orders.py --check          # scope + connectivity
    python3 scripts/poll_orders.py                  # fetch new, render tickets
    python3 scripts/poll_orders.py --since 2026-08-01

Needs `read_orders` on the app. Shopify also gates order payloads behind
protected customer data access, which for a custom app is a toggle in the dev
dashboard — see docs/LAUNCH.md.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

REPO = pathlib.Path(__file__).resolve().parent.parent
SYNC_REPO = REPO.parent / "coreyard"
ENV = pathlib.Path(os.environ.get("ABM_ENV", SYNC_REPO / ".env"))
STATE = REPO / ".orders_state.json"
ORDER_DIR = SYNC_REPO / "out" / "orders"
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


def gql(url, headers, query, variables=None, tries=5):
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
                time.sleep(2 * (attempt + 1))
                continue
            return {"__errors": errors}
        return payload.get("data") or {}
    raise RuntimeError("exhausted retries")


ORDERS = """
query($q: String!, $cursor: String) {
  orders(first: 25, after: $cursor, query: $q, sortKey: CREATED_AT, reverse: false) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      name
      createdAt
      email
      phone
      note
      displayFulfillmentStatus
      totalPriceSet { shopMoney { amount currencyCode } }
      shippingAddress {
        name address1 address2 city province zip country phone company
      }
      shippingLine { title }
      customer { firstName lastName }
      lineItems(first: 50) {
        nodes {
          name
          quantity
          sku
          variant { id }
          originalTotalSet { shopMoney { amount } }
        }
      }
    }
  }
}
"""


def rest_get(store: str, token: str, path: str, tries: int = 5) -> dict:
    """Fetch one order in the REST/webhook shape.

    The GraphQL query above is how we discover what is new, but it answers in camelCase
    (`lineItems`, `shippingAddress`). Everything downstream — the pull ticket renderer and
    yms/orders.from_shopify — parses the webhook payload Shopify actually POSTs, which is
    snake_case (`line_items`, `shipping_address`). Feeding the GraphQL shape to those is
    not an error, it is worse: from_shopify finds no line items, treats the order as
    "nothing of ours", and books nothing while reporting success.
    """
    url = f"https://{store}/admin/api/{API_VERSION}/{path}"
    req = urllib.request.Request(url, headers={"X-Shopify-Access-Token": token})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            if attempt < tries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    return {}


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"seen": [], "last_created": None}


def overlap_start(value: str) -> str:
    """Back up the high-water mark so equal timestamps remain retryable."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (parsed - timedelta(seconds=1)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return value


def save_state(seen: list[str], latest: str | None) -> None:
    state = {"seen": seen[-2000:], "last_created": latest}
    temporary = STATE.with_suffix(STATE.suffix + ".tmp")
    temporary.write_text(json.dumps(state))
    temporary.replace(STATE)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify scope and connectivity only")
    ap.add_argument("--since", help="ISO date to start from (default: last poll, else 2 days ago)")
    ap.add_argument("--no-ticket", action="store_true", help="save order JSON but skip rendering")
    ap.add_argument("--write-orders", action="store_true",
                    help="also book each order into the yard system (WRITES to PowerLink)")
    args = ap.parse_args()

    cfg = load_env()
    url = f"https://{cfg['SHOPIFY_STORE']}/admin/api/{API_VERSION}/graphql.json"
    headers = {"Content-Type": "application/json", "X-Shopify-Access-Token": cfg["SHOPIFY_ADMIN_TOKEN"]}

    scopes = gql(url, headers, "{ currentAppInstallation { accessScopes { handle } } }")
    granted = {s["handle"] for s in ((scopes.get("currentAppInstallation") or {}).get("accessScopes") or [])}
    has_orders = "read_orders" in granted
    print(f"read_orders granted: {has_orders}")
    if not has_orders:
        print("\n  Add read_orders (and write_orders if you want work-order write-back) to the")
        print("  app config, release a new version, and approve it. Shopify also requires")
        print("  protected customer data access for order payloads — that is a toggle on the")
        print("  app in the dev dashboard.")
        if args.check:
            return
        sys.exit(1)
    if args.check:
        print("ready to poll")
        return

    state = load_state()
    since = args.since or state.get("last_created") or (
        datetime.now(timezone.utc) - timedelta(days=2)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    seen_order = list(dict.fromkeys(state.get("seen") or []))
    seen = set(seen_order)

    ORDER_DIR.mkdir(parents=True, exist_ok=True)
    query_start = overlap_start(since)
    q = f"created_at:>'{query_start}'"
    cursor, new, latest = None, 0, state.get("last_created")
    failed = False

    while True:
        data = gql(url, headers, ORDERS, {"q": q, "cursor": cursor})
        if "__errors" in data:
            sys.exit(f"query failed: {json.dumps(data['__errors'])[:300]}")
        conn = data["orders"]
        for o in conn["nodes"]:
            if o["id"] in seen:
                continue
            path = ORDER_DIR / f"order-{o['name'].lstrip('#')}.json"
            # Save what the renderer and the yard writer can actually parse.
            order_id = str(o["id"]).rsplit("/", 1)[-1]
            payload = rest_get(cfg["SHOPIFY_STORE"], cfg["SHOPIFY_ADMIN_TOKEN"],
                               f"orders/{order_id}.json").get("order") or {}
            if not payload.get("line_items"):
                print(f"  ! {o['name']}: REST payload had no line items; skipping")
                failed = True
                break
            path.write_text(json.dumps(payload, indent=2))
            path.chmod(0o600)          # buyer name, address, phone, email
            addr = o.get("shippingAddress") or {}
            ship = (o.get("shippingLine") or {}).get("title") or "(pickup)"
            print(f"\n{o['name']}  {o['createdAt'][:16]}  {ship}")
            print(f"  to: {addr.get('company') or addr.get('name') or '(no address)'}"
                  f"  {addr.get('city') or ''} {addr.get('province') or ''}")
            for li in o["lineItems"]["nodes"]:
                print(f"    {li['quantity']} x {li['sku'] or '(no sku)':<14} {li['name'][:52]}")

            if not args.no_ticket:
                try:
                    cmd = [str(SYNC_REPO / ".venv/bin/python"), "-m", "coreyard.webhook",
                           "replay", str(path)]
                    if args.write_orders:
                        cmd.append("--write-order")
                    r = subprocess.run(cmd, cwd=SYNC_REPO, capture_output=True, text=True)
                except OSError as exc:
                    print(f"    ! ticket failed: {exc}")
                    failed = True
                    break
                if r.returncode == 0:
                    line = (r.stdout or "").strip().splitlines()
                    detail = next((x for x in line if "work order" in x), "")
                    print(f"    ticket rendered{' -> ' + detail.split('->')[-1].strip() if detail else ''}")
                else:
                    print(f"    ! ticket failed: {(r.stderr or r.stdout).strip()[:200]}")
                    failed = True
                    break

            seen.add(o["id"])
            seen_order.append(o["id"])
            if latest is None or o["createdAt"] > latest:
                latest = o["createdAt"]
            new += 1
            save_state(seen_order, latest)

        if failed or not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]

    save_state(seen_order, latest)
    print(f"\n{new} new order(s) completed since {since}")
    if new:
        print(f"tickets and JSON in {ORDER_DIR}")
    if failed:
        print("ticket rendering failed; the order was not acknowledged and will retry next poll")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
