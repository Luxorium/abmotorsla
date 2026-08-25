#!/usr/bin/env bash
# Put newly published variants into the right Shopify delivery profile.
#
# This used to also write the ship:* tag, which meant a part was live and sellable wearing
# no shipping classification until the next run. CoreYard now reads content/freight.json
# itself and applies the tag during the publish that creates the product, so the storefront
# warns correctly from the first second a part is buyable.
#
# What is left is genuinely Shopify's: a delivery profile is a set of variants, and a new
# variant lands in the default profile until something moves it. Until this runs, a freight
# part is correctly LABELLED as freight but would be CHARGED the ground rate at checkout, so
# keep it on a schedule.
#
#   scripts/finish_listings.sh --plan    # report drift, write nothing
#   scripts/finish_listings.sh --apply
#
# The catalog side of "unfinished" is CoreYard's:
#   coreyard reconcile --apply --activate   drafts that should be live, parts that came back
#   coreyard repair tags --apply            products published before a policy was configured
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

run "delivery profile" scripts/setup_shipping.py "$MODE"

exit "$status"
