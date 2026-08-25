#!/usr/bin/env python3
"""Build theme/assets/vehicles.json — the year/make/model index for the picker.

CoreYard tags every part with "<year> <make> <model>" (plus bare make and
"make model"), so the catalog already knows which vehicles we have parts for.
This distills that into make -> model -> [years] and writes it as a theme asset,
so the picker is instant and needs no API calls from the storefront.

Re-run it after a big sync. Building is read-only against Shopify; --deploy is the only
part that writes, and it writes exactly one asset onto the already-published theme.

Building alone changes nothing a shopper sees: the storefront serves this asset out of the
published theme, not out of this repo, so without --deploy the picker keeps offering
whatever vehicle list was live when the theme was last deployed by hand.

    python3 scripts/build_vehicles.py                    # build locally
    python3 scripts/build_vehicles.py --deploy --dry-run # what would change on the theme
    python3 scripts/build_vehicles.py --deploy           # build and push the asset live
"""
from __future__ import annotations

import argparse
import collections
import json
import re

from _shopify import REPO, Shopify

OUT = REPO / "theme" / "assets" / "vehicles.json"

# "2012 Honda Civic" -> year 2012, rest "Honda Civic"
YEAR_TAG = re.compile(r"^(19[5-9]\d|20[0-4]\d)\s+(.+)$")

# Makes as CoreYard writes them. A tag's first word is the make except for these
# two-word ones, where the model would otherwise swallow part of the make.
TWO_WORD_MAKES = {
    "land rover", "alfa romeo", "aston martin", "mercedes benz", "mercedes-benz",
    "rolls royce", "am general", "general motors",
}


SCAN = """
query($cursor: String) {
  products(first: 250, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes { tags }
  }
}
"""


# The yard writes one make several ways. CoreYard's vehicle label keeps whichever form
# the model carried, so "Mercedes-Benz" and "Mercedes" both reach the tags and would
# otherwise open two separate makes in the picker for the same cars.
MAKE_ALIASES = {
    "mercedes": "Mercedes-Benz",
    "mercedes benz": "Mercedes-Benz",
    "vw": "Volkswagen",
    "chevy": "Chevrolet",
}


def canonical_make(make: str, model: str) -> tuple[str, str]:
    """Fold an alias onto its canonical make, and drop a make the model repeats."""
    canon = MAKE_ALIASES.get(make.lower(), make)
    # "Mercedes-Benz" + "Mercedes 450" -> "Mercedes-Benz" + "450"
    head = canon.split("-")[0].split(" ")[0].lower()
    if head and model.lower().startswith(head + " "):
        model = model[len(head) + 1:]
    return canon, model


def split_make_model(rest: str) -> tuple[str, str] | None:
    low = rest.lower()
    for mk in TWO_WORD_MAKES:
        if low.startswith(mk + " "):
            return canonical_make(rest[: len(mk)], rest[len(mk) + 1:])
    parts = rest.split(" ", 1)
    if len(parts) != 2:
        return None
    return canonical_make(parts[0], parts[1])


MAIN_THEME_Q = "{ themes(first: 20) { nodes { id name role } } }"

LIVE_ASSET_Q = """
query($id: ID!, $name: String!) {
  theme(id: $id) {
    files(first: 1, filenames: [$name]) {
      nodes { body { ... on OnlineStoreThemeFileBodyText { content } } }
    }
  }
}
"""

UPSERT_ASSET = """
mutation($id: ID!, $files: [OnlineStoreThemeFilesUpsertFileInput!]!) {
  themeFilesUpsert(themeId: $id, files: $files) {
    upsertedThemeFiles { filename }
    userErrors { field message }
  }
}
"""


def deploy(gql: Shopify, payload: str, dry_run: bool) -> int:
    """Push the freshly built picker index onto the published theme.

    Building this file locally accomplishes nothing on its own: it is a theme asset, and the
    storefront reads the copy inside the *published theme*, not the one in this repo. Without
    this step the weekly rebuild updates a file nobody serves, and the picker keeps offering
    whatever vehicle list was current the last time somebody deployed the theme by hand —
    silently omitting every vehicle that has arrived in the yard since.

    Only the asset is written, and only onto the theme that is already live. This never
    publishes a theme, never changes which theme is published, and touches no other file.
    """
    themes = gql(MAIN_THEME_Q)["themes"]["nodes"]
    main = next((t for t in themes if t["role"] == "MAIN"), None)
    if not main:
        print("  no published (MAIN) theme found — not deploying")
        return 1
    name = f"assets/{OUT.name}"

    nodes = gql(LIVE_ASSET_Q, {"id": main["id"], "name": name}) \
        .get("theme", {}).get("files", {}).get("nodes") or []
    live = nodes[0]["body"]["content"] if nodes else None
    if live == payload:
        print(f"  live theme {main['name']!r} already has an identical {name}")
        return 0

    def combos(text):
        try:
            return {(mk, mo, y)
                    for mk, models in json.loads(text)["makes"].items()
                    for mo, years in models.items() for y in years}
        except Exception:
            return set()

    added = len(combos(payload) - combos(live or "")) if live else None
    detail = f", {added} new make/model/year combos" if added is not None else ""
    print(f"  target: {main['name']!r} ({name}{detail})")
    if dry_run:
        print("  --dry-run: not deploying")
        return 0

    res = gql(UPSERT_ASSET, {
        "id": main["id"],
        "files": [{"filename": name, "body": {"type": "TEXT", "value": payload}}],
    })
    # gql() raises on transport/GraphQL errors, so only per-file userErrors reach here.
    errs = (res.get("themeFilesUpsert") or {}).get("userErrors")
    if errs:
        print(f"  DEPLOY FAILED: {json.dumps(errs)[:200]}")
        return 1
    print(f"  deployed {name} to {main['name']!r}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--deploy", action="store_true",
                    help="upload the built asset onto the published theme (live storefront)")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --deploy, report what would change and write nothing")
    args = ap.parse_args()

    gql = Shopify.from_env()

    tree: dict[str, dict[str, set]] = collections.defaultdict(lambda: collections.defaultdict(set))
    parts_per_make = collections.Counter()
    cursor, n = None, 0

    print("scanning tags…", end="", flush=True)
    while True:
        conn = gql(SCAN, {"cursor": cursor})["products"]
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
                    # "<year> <make>" with no model: the yard has parts for the car but
                    # never recorded a model. Register the make anyway so the picker can
                    # still offer it — the facet applies a make-only tag filter when no
                    # model is chosen. Dropping it hid every Smart part from the picker.
                    # A make is a word, not a fragment: "Smart" qualifies, the stray
                    # "CX-" left by a model that lost its make does not.
                    letters = rest.replace("-", "").replace(".", "")
                    if (" " not in rest and len(rest) >= 3 and letters.isalpha()
                            and not rest.endswith("-")):
                        bare = MAKE_ALIASES.get(rest.lower(), rest)
                        tree[bare]            # touch: defaultdict creates the make
                        makes_here.add(bare)
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
    payload = json.dumps({"makes": dict(sorted(out.items()))}, separators=(",", ":"))
    OUT.write_text(payload)

    models = sum(len(m) for m in out.values())
    print(f"{n} products scanned")
    print(f"{len(out)} makes, {models} models -> {OUT.relative_to(REPO)} ({OUT.stat().st_size / 1024:.0f} KB)")
    print("\ntop makes by parts:")
    for mk, c in parts_per_make.most_common(12):
        if mk in out:
            print(f"  {mk:<20}{c:>5} parts, {len(out[mk]):>3} models")

    if args.deploy:
        print("\ndeploying to the published theme…")
        raise SystemExit(deploy(gql, payload, args.dry_run))
    else:
        print("\nbuilt locally only. The storefront reads this asset from the published "
              "theme, so re-run with --deploy to make the picker actually see it.")


if __name__ == "__main__":
    main()
