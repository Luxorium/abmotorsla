"""The storefront's Shopify client — one implementation, shared by every script here.

These scripts configure the *store*: collections, pages, policies, delivery profiles,
shipping tags, the vehicle index. They are not the catalog pipeline; that is CoreYard's job
and it has its own tested client. What they do share with each other is the same handful of
mechanics — auth, API version, retry, throttle backoff, cursor paging — and each script used
to carry its own slightly different copy of all four.

Nothing here imports CoreYard. The two repositories talk through Shopify, through
configuration files, and through CoreYard's CLI; a `sys.path` hack into a sibling checkout
makes a storefront script fail when a backend module moves, which is a coupling neither side
asked for.

Credentials come from a `.env` file that is never committed:

    ABM_ENV=/path/to/.env      explicit, wins over everything
    abmotorsla/.env            this repository's own file (preferred)
    ../coreyard/.env           the backend's file, for installations set up before this
                               repository had one

Only SHOPIFY_STORE and SHOPIFY_ADMIN_TOKEN are read.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Iterator

REPO = pathlib.Path(__file__).resolve().parent.parent
API_VERSION = os.environ.get("ABM_API_VERSION", "2026-07")

# Shopify's GraphQL budget refills continuously. Dropping below this many points means the
# next calls are about to be throttled, so pause briefly rather than earn a 429.
THROTTLE_FLOOR = 200

_lock = threading.Lock()


def env_path() -> pathlib.Path:
    explicit = os.environ.get("ABM_ENV")
    if explicit:
        return pathlib.Path(explicit)
    local = REPO / ".env"
    if local.exists():
        return local
    return REPO.parent / "coreyard" / ".env"


def load_env(path: pathlib.Path | None = None) -> dict:
    """Read KEY=VALUE lines. Exits with an explanation rather than a traceback."""
    target = path or env_path()
    if not target.exists():
        sys.exit(
            f"no .env at {target}\n"
            f"Create {REPO / '.env'} with SHOPIFY_STORE and SHOPIFY_ADMIN_TOKEN, "
            f"or set ABM_ENV to point at one."
        )
    cfg: dict[str, str] = {}
    for line in target.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            cfg[key.strip()] = value.strip().strip('"').strip("'")
    return cfg


class Shopify:
    """A minimal Admin GraphQL client: retries, throttle backoff, and cursor paging."""

    def __init__(self, store: str, token: str, api_version: str = API_VERSION):
        self.store = store
        self.url = f"https://{store}/admin/api/{api_version}/graphql.json"
        self.headers = {"Content-Type": "application/json",
                        "X-Shopify-Access-Token": token}
        self._pause = 0.0

    @classmethod
    def from_env(cls, cfg: dict | None = None) -> "Shopify":
        cfg = cfg or load_env()
        missing = [k for k in ("SHOPIFY_STORE", "SHOPIFY_ADMIN_TOKEN") if not cfg.get(k)]
        if missing:
            sys.exit(f"{env_path()} is missing: {', '.join(missing)}")
        return cls(cfg["SHOPIFY_STORE"], cfg["SHOPIFY_ADMIN_TOKEN"])

    def __call__(self, query: str, variables: dict | None = None, tries: int = 6) -> dict:
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        for attempt in range(tries):
            wait = self._pause - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                request = urllib.request.Request(self.url, data=body, headers=self.headers)
                with urllib.request.urlopen(request, timeout=90) as reply:
                    payload = json.loads(reply.read())
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
                if attempt < tries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
            cost = (((payload.get("extensions") or {}).get("cost") or {})
                    .get("throttleStatus") or {})
            if cost.get("currentlyAvailable", 1000) < THROTTLE_FLOOR:
                with _lock:
                    self._pause = max(self._pause, time.monotonic() + 1.0)
            errors = payload.get("errors") or []
            if errors:
                codes = {(e.get("extensions") or {}).get("code") for e in errors}
                # THROTTLED is Shopify asking us to slow down. INTERNAL_SERVER_ERROR is
                # Shopify failing on its own account: it arrives as a 200 with an error
                # body, so the transport retry above never sees it, and the caller used to
                # die on the spot. Seen on 2026-09-04, where three consecutive
                # `metafieldsSet` calls in `store_setup.py --pages` failed with fresh
                # request IDs while the identical mutation, run on its own, succeeded.
                #
                # Retrying a mutation is only safe because every mutation in this
                # repository is an upsert or an idempotent update — metafieldsSet,
                # pageUpdate, themeFilesUpsert. If a create without an idempotency key is
                # ever added here, exclude it rather than widening this.
                retryable = codes & {"THROTTLED", "INTERNAL_SERVER_ERROR"}
                if retryable and attempt < tries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise RuntimeError(json.dumps(errors)[:400])
            return payload.get("data") or {}
        raise RuntimeError("exhausted retries")

    def mutate(self, document: str, variables: dict, root: str) -> dict:
        """Run a mutation and raise on `userErrors`.

        Shopify reports a refused write as a 200 with `userErrors` filled in, so a caller
        that only watches for exceptions treats "I did not do that" as success.
        """
        result = self(document, variables)[root] or {}
        errors = result.get("userErrors") or []
        if errors:
            raise RuntimeError(f"{root}: {json.dumps(errors)[:300]}")
        return result

    def paginate(self, query: str, connection: str, variables: dict | None = None,
                 page_size: int = 250) -> Iterator[dict]:
        """Walk a Relay connection. The query takes $cursor and $first and selects nodes."""
        cursor = None
        while True:
            page = self(query, {**(variables or {}), "cursor": cursor,
                                "first": page_size})[connection]
            yield from page["nodes"]
            if not page["pageInfo"]["hasNextPage"]:
                return
            cursor = page["pageInfo"]["endCursor"]
