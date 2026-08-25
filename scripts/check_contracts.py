#!/usr/bin/env python3
"""Check that this repository's configuration still agrees with itself and with the theme.

Three systems have to hold the same opinion about how a part ships: the delivery profiles
that decide what checkout charges, the ship:* tag CoreYard writes onto the product, and the
Liquid that warns a shopper before they get there. When they drift, the failure is silent
and expensive — a 605 lb engine quoting free ground, or a door that says "free shipping" and
then offers no shipping method at all.

content/freight.json is the one place that decides. This checks that everything derived from
it still matches, using nothing but the standard library:

    python3 scripts/check_contracts.py

It deliberately does NOT import CoreYard. The backend validates the same files against its
own schemas with `coreyard validate`, which CI runs separately against a checkout of that
repository — see .github/workflows/contracts.yml. This half is the checks only the storefront
can make, because only the storefront knows what its theme says.

Read-only. No credentials, no network.
"""
from __future__ import annotations

import json
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
CONTENT = REPO / "content"
THEME = REPO / "theme"

# The two snippets allowed to know a ship:* tag exists. Everything else must ask them.
CLASS_SNIPPET = THEME / "snippets" / "shipping-class.liquid"
GROUP_SNIPPET = THEME / "snippets" / "shipping-group.liquid"
CONFIG_SNIPPET = THEME / "snippets" / "shipping-config.liquid"

# Generated assets: checked for the marker that says so, not for freshness, because
# regenerating needs the live catalogue.
GENERATED = {THEME / "assets" / "vehicles.json": "scripts/build_vehicles.py"}

# Namespaces the theme may still read besides the configured one. `custom` held grades
# entered by hand before CoreYard published any metafields; card-product falls back to it so
# those products keep showing a grade until a full sync has republished them with abm.grade.
# Delete the fallback and this entry together once that has happened.
LEGACY_NAMESPACES = {"custom"}

_TAG = re.compile(r"ship:[a-z0-9][a-z0-9-]*")
_COMMENT = re.compile(r"\{%-?\s*comment\s*-?%\}.*?\{%-?\s*endcomment\s*-?%\}", re.S)
_MONEY = re.compile(r"\$(\d+\.\d{2})")


class Checker:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.notes: list[str] = []

    def fail(self, message: str) -> None:
        self.errors.append(message)

    def note(self, message: str) -> None:
        self.notes.append(message)

    def report(self) -> int:
        for line in self.notes:
            print(f"  {line}")
        if self.errors:
            print("\nContract check FAILED:\n")
            for line in self.errors:
                print(f"  ✗ {line}")
            return 1
        print("\nContract check passed.")
        return 0


def load_json(path: pathlib.Path, check: Checker):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        check.fail(f"{path.relative_to(REPO)} is missing")
    except json.JSONDecodeError as exc:
        check.fail(f"{path.relative_to(REPO)} is not valid JSON: {exc}")
    return None


def check_all_json(check: Checker) -> None:
    """Every JSON file in the repository parses. A broken one breaks a deploy."""
    bad = 0
    for path in sorted(list(CONTENT.rglob("*.json")) + list(THEME.rglob("*.json"))):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            check.fail(f"{path.relative_to(REPO)}: {exc}")
            bad += 1
    check.note(f"JSON: every file parses" if not bad else f"JSON: {bad} file(s) broken")


def check_shipping(check: Checker) -> dict:
    """The shipping policy is internally consistent, before anything derives from it."""
    data = load_json(CONTENT / "freight.json", check)
    if not isinstance(data, dict):
        return {}
    groups = data.get("groups")
    if not isinstance(groups, dict) or not groups:
        check.fail("freight.json has no 'groups' object")
        return {}

    tags: dict[str, str] = {}
    defaults: list[str] = []
    for gid, body in groups.items():
        if not isinstance(body, dict):
            check.fail(f"freight.json group {gid!r} is not an object")
            continue
        tag = str(body.get("tag") or "").strip()
        if not tag:
            check.fail(f"freight.json group {gid!r} has no 'tag'")
            continue
        if tag.lower() in tags:
            check.fail(f"freight.json: groups {tags[tag.lower()]!r} and {gid!r} share the "
                       f"tag {tag!r}; a product would carry two classifications at once")
        tags[tag.lower()] = gid
        if body.get("default"):
            defaults.append(gid)
        fulfillment = str(body.get("fulfillment") or "")
        if fulfillment not in ("", "external", "yard"):
            check.fail(f"freight.json group {gid!r}: fulfillment {fulfillment!r} must be "
                       f"'external', 'yard', or omitted")
        price = body.get("price", "missing")
        if price != "missing" and price is not None:
            if not re.fullmatch(r"\d+\.\d{2}", str(price)):
                check.fail(f"freight.json group {gid!r}: price {price!r} should be a "
                           f"decimal string like \"299.99\", or null for 'not shipped'")
    if len(defaults) != 1:
        check.fail(f"freight.json must have exactly one group with \"default\": true; "
                   f"found {len(defaults)}")

    order = data.get("match_order") or []
    unknown = [g for g in order if g not in groups]
    if unknown:
        check.fail(f"freight.json 'match_order' names unknown group(s): {', '.join(unknown)}")
    for gid, body in groups.items():
        if isinstance(body, dict) and body.get("match") and gid not in order:
            check.fail(f"freight.json group {gid!r} has 'match' patterns but is not in "
                       f"'match_order', so it can never be matched")
    check.note(f"shipping: {len(groups)} group(s), tags {sorted(tags)}")
    return data


def check_order_policy(check: Checker, shipping: dict) -> None:
    """Any shipping group the order policy names still has to exist."""
    data = load_json(CONTENT / "order-sync.json", check)
    if not isinstance(data, dict):
        return
    fulfillment = data.get("fulfillment")
    if not isinstance(fulfillment, dict):
        check.note("order policy: no fulfillment block, so it is derived from freight.json")
        return
    known = {str(b.get("tag", "")).lower()
             for b in (shipping.get("groups") or {}).values() if isinstance(b, dict)}
    named = list(fulfillment.get("groups") or []) + list(fulfillment.get("defer_groups") or [])
    if fulfillment.get("default_group"):
        named.append(fulfillment["default_group"])
    for name in named:
        if str(name).lower() not in known:
            check.fail(f"order-sync.json names shipping group {name!r}, which is not a tag "
                       f"in freight.json")
    check.note("order policy: declares its own fulfillment block (freight.json's "
               "'fulfillment' keys are then ignored)")


def check_theme(check: Checker, shipping: dict) -> None:
    """The theme's copy of the contract matches, and only the two snippets hold one."""
    groups = shipping.get("groups") or {}
    declared = {str(b.get("tag", "")).lower()
                for b in groups.values() if isinstance(b, dict) and b.get("tag")}
    if not declared:
        return

    for snippet in (CLASS_SNIPPET, GROUP_SNIPPET, CONFIG_SNIPPET):
        if not snippet.exists():
            check.fail(f"{snippet.relative_to(REPO)} is missing")
            return

    used = set(_TAG.findall(CLASS_SNIPPET.read_text(encoding="utf-8").lower()))
    unknown = used - declared
    if unknown:
        check.fail(f"shipping-class.liquid tests for {sorted(unknown)}, which freight.json "
                   f"does not declare")
    # The default group is never tested for; it is what the snippet falls through to.
    missing = declared - used - {_default_tag(groups)}
    if missing:
        check.fail(f"freight.json declares {sorted(missing)} but shipping-class.liquid never "
                   f"tests for them, so those parts fall through to the default")

    # Nothing else in the theme may test a ship:* tag: that is how six copies of the same
    # if/elsif chain appeared, four of which also quoted a rate.
    allowed = {CLASS_SNIPPET, GROUP_SNIPPET, CONFIG_SNIPPET}
    for path in sorted(THEME.rglob("*.liquid")):
        if path in allowed:
            continue
        for line in _without_comments(path.read_text(encoding="utf-8")).splitlines():
            if _TAG.search(line):
                check.fail(f"{path.relative_to(REPO)} tests a ship:* tag directly; render "
                           f"'shipping-class' instead: {line.strip()[:80]}")

    # Rates quoted to a shopper must be the rates checkout charges.
    prices = {str(b.get("price")) for b in groups.values()
              if isinstance(b, dict) and b.get("price") not in (None, "0.00")}
    for snippet in (GROUP_SNIPPET, CONFIG_SNIPPET):
        quoted = set(_MONEY.findall(snippet.read_text(encoding="utf-8")))
        extra = quoted - prices
        if extra:
            check.fail(f"{snippet.relative_to(REPO)} quotes {sorted(extra)}, which is not a "
                       f"price in freight.json ({sorted(prices)})")
        absent = prices - quoted
        if snippet is GROUP_SNIPPET and absent:
            check.fail(f"freight.json charges {sorted(absent)} but shipping-group.liquid "
                       f"never says so")
    check.note(f"theme: rates {sorted(prices)} match, ship:* tested in one snippet")


def _without_comments(source: str) -> str:
    """Liquid with its comment blocks removed, so prose about a tag is not mistaken for one."""
    return _COMMENT.sub("", source)


def _default_tag(groups: dict) -> str:
    for body in groups.values():
        if isinstance(body, dict) and body.get("default"):
            return str(body.get("tag", "")).lower()
    return ""


def check_metafield_namespace(check: Checker) -> None:
    """The theme reads a namespace; the profile tells CoreYard which one to write."""
    profile = load_json(CONTENT / "catalog-profile.json", check)
    if not isinstance(profile, dict):
        return
    namespace = str(profile.get("metafield_namespace") or "").strip()
    if not namespace:
        check.fail("catalog-profile.json does not set 'metafield_namespace', so CoreYard "
                   "publishes into its neutral default and the theme reads nothing")
        return
    used = set()
    for path in THEME.rglob("*.liquid"):
        used.update(re.findall(r"metafields\.([a-z0-9_]+)\.",
                               path.read_text(encoding="utf-8")))
    unknown = used - {namespace} - LEGACY_NAMESPACES
    if unknown:
        check.fail(f"the theme reads metafield namespace(s) {sorted(unknown)} but "
                   f"catalog-profile.json publishes into {namespace!r}")
    legacy = sorted(used & LEGACY_NAMESPACES)
    check.note(f"metafields: theme and profile agree on {namespace!r}"
               + (f" (still falling back to {legacy} during migration)" if legacy else ""))


def check_generated(check: Checker) -> None:
    for path, builder in GENERATED.items():
        if not path.exists():
            check.fail(f"{path.relative_to(REPO)} is missing (build it with {builder})")
            continue
        check.note(f"generated: {path.relative_to(REPO)} ({path.stat().st_size // 1024} KB, "
                   f"rebuild with {builder})")


def main() -> int:
    print(f"Checking contracts in {REPO}\n")
    check = Checker()
    check_all_json(check)
    shipping = check_shipping(check)
    check_order_policy(check, shipping)
    check_theme(check, shipping)
    check_metafield_namespace(check)
    check_generated(check)
    return check.report()


if __name__ == "__main__":
    raise SystemExit(main())
