#!/usr/bin/env bash
# Finish newly published listings on the storefront side: delivery profile and ship:* tag.
#
# CoreYard publishes a product with its title, price, photos, SEO, inventory, shipping
# weight, and (with STORE_PUBLICATIONS set) its Online Store publication. What it cannot
# know is how *this* yard ships a given part type — that classification lives in
# content/freight.json and is A&B's own commercial policy, so the two steps below stay here.
#
# Publishing is continuous, so finishing has to be too. Each step re-scans the catalog and
# writes only what is missing, so running this repeatedly is cheap and idempotent.
#
#   scripts/finish_listings.sh --plan    # report drift, write nothing
#   scripts/finish_listings.sh --apply
#
# The catalog side of "unfinished" is CoreYard's:
#   coreyard reconcile --apply --activate   drafts that should be live, parts that came back
#   coreyard repair weights --apply         products published before weights were owned
#   coreyard audit catalog                  what is still missing, across the whole catalog
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${ABM_PYTHON:-python3}"

case "${1:---plan}" in
  --plan)  MODE=--plan  ;;
  --apply) MODE=--apply ;;
  *) echo "usage: $0 [--plan|--apply]" >&2; exit 2 ;;
esac

cd "$REPO" || exit 1
status=0

run() {
  local label="$1"; shift
  echo "===== $label ====="
  if ! "$PY" "$@"; then
    echo "!! $label FAILED (exit $?)" >&2
    status=1
  fi
}

# The profile decides what checkout charges; the tag is what the theme can see, so it can
# warn a shopper on the product page instead of at a dead checkout. Profile first: a wrong
# charge costs a sale immediately, a missing warning only costs a phone call.
run "delivery profile" scripts/setup_shipping.py "$MODE"
run "shipping tags"    scripts/tag_shipping.py   "$MODE"

# A failure in one step must not hide the other: they are independent, and the next run
# retries whatever did not land. Exit non-zero so a health check notices.
exit "$status"
