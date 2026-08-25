#!/usr/bin/env python3
"""Push this repository's theme code onto the published theme, over the Admin API.

`shopify theme push` is the normal way to do this and needs no explanation. This exists for
hosts without the Shopify CLI — the same `themeFilesUpsert` mutation build_vehicles.py
already uses to deploy the vehicle index.

It pushes **code**, not configuration. Everything the theme editor owns is excluded by
default:

    config/settings_data.json     what a merchant set in Theme settings
    templates/*.json              section layout and content per template
    sections/*-group.json         header and footer group content
    locales/*.json                translations

Those files differ between the repo and the live theme as a matter of course, because the
editor writes them and nobody exports them back. Pushing the repo's copy would silently
throw away whatever was configured in admin, which is the one mistake a theme deploy can
make that nobody notices until a customer does.

    python3 scripts/deploy_theme.py             # plan: what would change, per file
    python3 scripts/deploy_theme.py --apply
    python3 scripts/deploy_theme.py --apply --only snippets/shipping-group.liquid
    python3 scripts/deploy_theme.py --apply --include-config   # deliberate, rarely right

Every file it overwrites is backed up first, to a timestamped directory it prints. Restore
with --restore <dir>. It never publishes, unpublishes, or creates a theme; it only writes
files onto the theme that is already live.
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import fnmatch
import json
import pathlib
import sys

from _shopify import REPO, Shopify

THEME = REPO / "theme"
BACKUP_ROOT = REPO / ".theme-backups"

# Owned by the theme editor, not by this repository. See the module docstring.
EDITOR_OWNED = (
    "config/settings_data.json",
    "templates/*.json",
    "sections/*-group.json",
    "locales/*.json",
)

# Not theme files at all.
NEVER = ("shopify.theme.toml",)

THEMES_Q = "{ themes(first: 25) { nodes { id name role } } }"

FILES_Q = """query($id:ID!,$cursor:String){
  theme(id:$id){ files(first:250, after:$cursor){
    pageInfo{ hasNextPage endCursor }
    nodes{ filename body{ ... on OnlineStoreThemeFileBodyText { content } } } } } }"""

UPSERT_M = """mutation($id:ID!,$files:[OnlineStoreThemeFilesUpsertFileInput!]!){
  themeFilesUpsert(themeId:$id, files:$files){
    upsertedThemeFiles{ filename }
    userErrors{ filename message } } }"""


def editor_owned(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in EDITOR_OWNED)


def published_theme(gql: Shopify) -> dict:
    themes = gql(THEMES_Q)["themes"]["nodes"]
    main = next((t for t in themes if t["role"] == "MAIN"), None)
    if not main:
        sys.exit("no published (MAIN) theme on this store")
    return main


def live_files(gql: Shopify, theme_id: str) -> dict[str, str]:
    out, cursor = {}, None
    while True:
        page = gql(FILES_Q, {"id": theme_id, "cursor": cursor})["theme"]["files"]
        for node in page["nodes"]:
            out[node["filename"]] = (node.get("body") or {}).get("content")
        if not page["pageInfo"]["hasNextPage"]:
            return out
        cursor = page["pageInfo"]["endCursor"]


def local_files() -> dict[str, str]:
    out = {}
    for path in sorted(THEME.rglob("*")):
        if not path.is_file():
            continue
        name = str(path.relative_to(THEME))
        if name in NEVER:
            continue
        try:
            out[name] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue          # a binary asset; the editor uploads those
    return out


def restore(gql: Shopify, theme: dict, directory: pathlib.Path) -> int:
    root = pathlib.Path(directory)
    if not root.is_dir():
        sys.exit(f"no backup directory at {root}")
    files = [(str(p.relative_to(root)), p.read_text(encoding="utf-8"))
             for p in sorted(root.rglob("*")) if p.is_file()]
    if not files:
        sys.exit(f"{root} holds no files")
    print(f"restoring {len(files)} file(s) onto {theme['name']!r}:")
    for name, _ in files:
        print(f"  {name}")
    result = gql(UPSERT_M, {"id": theme["id"], "files": [
        {"filename": n, "body": {"type": "TEXT", "value": t}} for n, t in files]})
    errors = result["themeFilesUpsert"]["userErrors"]
    if errors:
        print(f"FAILED: {json.dumps(errors)[:400]}", file=sys.stderr)
        return 1
    print("restored")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="write the files (default: plan)")
    ap.add_argument("--only", nargs="*", default=None,
                    help="deploy just these paths, relative to theme/")
    ap.add_argument("--include-config", action="store_true",
                    help="also push the files the theme editor owns (overwrites admin work)")
    ap.add_argument("--restore", default=None, metavar="DIR",
                    help="put a backup directory back onto the published theme")
    args = ap.parse_args()

    gql = Shopify.from_env()
    theme = published_theme(gql)
    print(f"published theme: {theme['name']!r}\n")

    if args.restore:
        return restore(gql, theme, pathlib.Path(args.restore))

    live = live_files(gql, theme["id"])
    local = local_files()

    candidates = sorted(args.only) if args.only else sorted(local)
    missing = [n for n in candidates if n not in local]
    if missing:
        sys.exit(f"not in theme/: {', '.join(missing)}")

    to_write, held_back = [], []
    for name in candidates:
        if editor_owned(name) and not args.include_config:
            if live.get(name) != local[name]:
                held_back.append(name)
            continue
        current = live.get(name)
        if current == local[name]:
            continue
        to_write.append((name, current, local[name]))

    if not to_write:
        print("Nothing to deploy — every code file already matches the live theme.")
        if held_back:
            print(f"\n({len(held_back)} theme-editor file(s) differ and were left alone.)")
        return 0

    print(f"{len(to_write)} file(s) to write:\n")
    for name, current, new in to_write:
        if current is None:
            print(f"  + {name:<40} new")
            continue
        added = sum(1 for line in difflib.unified_diff(
            current.splitlines(), new.splitlines(), lineterm="") if line.startswith("+"))
        removed = sum(1 for line in difflib.unified_diff(
            current.splitlines(), new.splitlines(), lineterm="") if line.startswith("-"))
        print(f"  ~ {name:<40} +{added - 1} -{removed - 1} lines")

    if held_back:
        print(f"\n{len(held_back)} file(s) the theme editor owns differ and were NOT touched.")
        for name in held_back:
            print(f"    {name}")
        print("  Push them with --include-config only if you mean to overwrite admin work.")

    if not args.apply:
        print("\nplan only — nothing written. Re-run with --apply.")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = BACKUP_ROOT / stamp
    for name, current, _ in to_write:
        if current is None:
            continue                      # nothing to restore a new file to
        destination = backup / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(current, encoding="utf-8")
    if backup.exists():
        print(f"\nbacked up the live copies -> {backup.relative_to(REPO)}")
        print(f"  undo with: python3 scripts/deploy_theme.py --restore {backup}")

    result = gql(UPSERT_M, {"id": theme["id"], "files": [
        {"filename": n, "body": {"type": "TEXT", "value": t}} for n, _, t in to_write]})
    node = result["themeFilesUpsert"]
    if node["userErrors"]:
        print(f"\nFAILED: {json.dumps(node['userErrors'])[:400]}", file=sys.stderr)
        return 1
    print(f"\nwrote {len(node['upsertedThemeFiles'])} file(s) to {theme['name']!r}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
