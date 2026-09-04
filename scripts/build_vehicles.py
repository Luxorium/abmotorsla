#!/usr/bin/env python3
"""Build theme/assets/vehicles.json — the year/make/model index for the picker.

CoreYard publishes each part's fitment as a structured metafield (`abm.fitment`: a list of
`{years, make, model, note, label}`), so the picker is built from data with named fields.

It used to be built by parsing CoreYard's display tags — "2014 Ford F-150" split on the
first space, with a hand-maintained list of two-word makes so "Land Rover" and "American
Motors" did not lose half their name to the model. That list could only ever be as complete
as somebody remembered: the live index still contains a make called "American" whose models
are "Motors Hummer H2" and "Motors Hummer H3". Worse, it made the storefront's navigation
depend on the exact wording of CoreYard's SEO tags, so improving a title could silently
break the picker.

Tags are still what *filters* a collection — Shopify has no way to facet on a metafield in a
collection URL — but they are no longer how the vehicle list is discovered.

    python3 scripts/build_vehicles.py               # build locally
    python3 scripts/build_vehicles.py --deploy      # upload onto the published theme
    python3 scripts/build_vehicles.py --deploy --dry-run

Building is read-only against Shopify; --deploy writes one theme asset and nothing else.
"""
from __future__ import annotations

import argparse
import collections
import json
import re

from _shopify import REPO, Shopify

OUT = REPO / "theme" / "assets" / "vehicles.json"

# The namespace CoreYard publishes into, from content/catalog-profile.json.
PROFILE = REPO / "content" / "catalog-profile.json"

# The yard writes one make several ways and CoreYard passes that through, so "Mercedes-Benz"
# and "Mercedes" would open two entries in the picker for the same cars. This folds them.
# It is a small normalization table, not a parser: the make arrives as its own field.
MAKE_ALIASES = {
    "mercedes": "Mercedes-Benz",
    "mercedes benz": "Mercedes-Benz",
    "vw": "Volkswagen",
    "chevy": "Chevrolet",
}

# A rebuild that sees far less fitment than the catalogue has is a rebuild against a
# half-migrated store, and writing its result would empty the picker.
MIN_COVERAGE = 0.60

SCAN = """
query($cursor: String, $ns: String!, $key: String!) {
  products(first: 250, after: $cursor, query: "status:active") {
    pageInfo { hasNextPage endCursor }
    nodes { id tags metafield(namespace: $ns, key: $key) { value } }
  }
}
"""


def namespace() -> str:
    """The metafield namespace this site configured CoreYard to publish into."""
    try:
        return json.loads(PROFILE.read_text()).get("metafield_namespace") or "abm"
    except (OSError, json.JSONDecodeError):
        return "abm"


def canonical_make(make: str, model: str) -> tuple[str, str]:
    """Fold an alias onto its canonical make, and drop a make the model repeats."""
    canon = MAKE_ALIASES.get(make.strip().lower(), make.strip())
    head = canon.split("-")[0].split(" ")[0].lower()
    if head and model.lower().startswith(head + " "):
        model = model[len(head) + 1:]
    return canon, model.strip()


def handleize(text: str) -> str:
    """Shopify's own tag handleizing, which is what a /collections/all/<tag> URL is keyed on."""
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", str(text).lower()))


def tag_make(year: str, make: str, model: str, tags: list[str], known: set) -> str | None:
    """The make spelling CoreYard actually tagged this vehicle with, or None if it did not.

    The picker's links are tag URLs, but the picker is built from the fitment metafield, and
    the two do not always spell a make the same way: fitment says "Mercedes-Benz" where the
    tag says "2016 Mercedes GLE-Class", and the yard's regional splits ("BMW - US Additional")
    never reach a tag at all. Shopify answers an unknown tag by dropping the filter, so every
    one of those mismatches sent a shopper to the whole 22,665-part catalogue.

    Nothing here is a hand-kept spelling table. The product carries both halves, so the tag
    is read off the product itself: an exact hit first, otherwise the one tag that pairs this
    year with this model, whose middle is then the make. A learned make that merely extends a
    make we already know ("Chevrolet Silverado") is a greedy match against a model, not a make.
    """
    if f"{year} {make} {model}".strip() in tags:
        return make
    if model:
        pattern = re.compile(rf"^{re.escape(year)}\s+(.+?)\s+{re.escape(model)}$")
        for tag in tags:
            found = pattern.match(tag)
            if not found:
                continue
            learned = found.group(1).strip()
            if any(learned != k and learned.lower().startswith(k.lower() + " ") for k in known):
                continue
            return learned
    # Makes the yard tags without a model at all, such as "2016 Smart".
    if f"{year} {make}" in tags:
        return ""
    return None


def years_of(value: str) -> list[int]:
    """Expand a fitment row's year label: "2011-2014" -> [2011..2014], "2014" -> [2014]."""
    text = str(value or "").strip()
    if not text:
        return []
    bounds = [p.strip() for p in text.split("-", 1)]
    try:
        start = int(bounds[0])
        end = int(bounds[1]) if len(bounds) > 1 and bounds[1] else start
    except ValueError:
        return []
    if end < start:
        start, end = end, start
    # A span wider than a normal production run means a placeholder reached the data;
    # listing 80 years in the picker would be worse than listing none.
    if end - start > 40:
        return []
    return list(range(start, end + 1))


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
    ap.add_argument("--allow-partial", action="store_true",
                    help="build even when most products have no structured fitment yet")
    args = ap.parse_args()

    gql = Shopify.from_env()
    ns, key = namespace(), "fitment"

    tree: dict[str, dict[str, set]] = collections.defaultdict(
        lambda: collections.defaultdict(set))
    parts_per_make: collections.Counter = collections.Counter()
    cursor, n, with_fitment, malformed = None, 0, 0, 0
    all_tags: set[str] = set()          # every tag in the catalogue, handleized
    fitment_makes: set[str] = set()     # makes as the metafield spells them
    untagged = 0                        # year rows with no tag to filter on

    print(f"reading {ns}.{key} metafields…", end="", flush=True)
    while True:
        conn = gql(SCAN, {"cursor": cursor, "ns": ns, "key": key})["products"]
        for product in conn["nodes"]:
            n += 1
            raw = (product.get("metafield") or {}).get("value")
            if not raw:
                continue
            try:
                rows = json.loads(raw)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(rows, list):
                malformed += 1
                continue
            with_fitment += 1
            tags = list(product.get("tags") or [])
            all_tags.update(handleize(t) for t in tags)
            makes_here = set()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                make, model = canonical_make(str(row.get("make") or ""),
                                             str(row.get("model") or ""))
                if len(make) < 2:
                    continue
                fitment_makes.add(make)
                for year in years_of(row.get("years")):
                    # Register the vehicle under the make its tag uses, not the one the
                    # metafield uses, and skip a year the yard never tagged: the picker
                    # links to that tag, so an entry it cannot filter on is worse than no
                    # entry at all.
                    tagged = tag_make(str(year), make, model, tags, fitment_makes)
                    if tagged is None:
                        untagged += 1
                        continue
                    if tagged and model:
                        tree[tagged][model].add(year)
                    else:
                        # The yard has parts for the make but never recorded a model.
                        # Register the make anyway: the picker applies a make-only tag
                        # filter when no model is chosen, and dropping it hid every Smart part.
                        tagged = tagged or make
                        tree[tagged]
                    makes_here.add(tagged)
            for make in makes_here:
                parts_per_make[make] += 1
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]
        if n % 2000 < 250:
            print(".", end="", flush=True)
    print(" done")

    coverage = with_fitment / n if n else 0.0
    print(f"{n} active products, {with_fitment} carry structured fitment "
          f"({coverage:.0%})" + (f", {malformed} malformed" if malformed else ""))
    if n and coverage < MIN_COVERAGE and not args.allow_partial:
        print(f"\nREFUSING to build: only {coverage:.0%} of the catalogue has fitment, "
              f"below the {MIN_COVERAGE:.0%} floor.\nThat is what a half-migrated store "
              f"looks like — CoreYard publishes the metafield during a normal sync, so run "
              f"one first.\nRe-run with --allow-partial if the catalogue really is like "
              f"this.")
        raise SystemExit(1)

    # Every level the picker can link to is checked against a tag that exists, because
    # each one is a separate URL: choosing a model alone builds "mercedes c-class" and
    # choosing a make alone builds "ford". A level with no tag is dropped rather than
    # offered, so the picker cannot promise a filter the catalogue will not honour.
    out, dropped_models, dropped_years = {}, 0, 0
    for make, models in tree.items():
        # Drop one-off noise: a make needs a couple of parts to be worth listing.
        if parts_per_make[make] < 2:
            continue
        keep = {}
        for model, years in sorted(models.items()):
            if handleize(f"{make} {model}") not in all_tags:
                dropped_models += 1
                continue
            usable = sorted(y for y in years
                            if handleize(f"{y} {make} {model}") in all_tags)
            dropped_years += len(years) - len(usable)
            if usable:
                keep[model] = usable
            else:
                dropped_models += 1
        if keep or handleize(make) in all_tags:
            out[make] = keep

    # A make whose own tag exists can be filtered on alone; the rest need a model, and
    # the theme keeps the button disabled until it has one.
    make_only = sorted(m for m in out if handleize(m) in all_tags)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"makes": dict(sorted(out.items())), "make_only": make_only},
                         separators=(",", ":"))
    OUT.write_text(payload)

    models = sum(len(m) for m in out.values())
    print(f"{len(out)} makes, {models} models -> {OUT.relative_to(REPO)} ({OUT.stat().st_size / 1024:.0f} KB)")
    print(f"  {len(make_only)} makes filterable on their own; "
          f"{len(out) - len(make_only)} need a model")
    if untagged or dropped_models or dropped_years:
        print(f"  skipped as untaggable: {untagged} year rows, "
              f"{dropped_models} models, {dropped_years} model-years")
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
