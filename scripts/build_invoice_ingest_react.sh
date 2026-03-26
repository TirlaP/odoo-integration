#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${ROOT_DIR}/custom_addons/automotive_parts/static/src/tsx/invoice_ingest_react_page.tsx"
OUT="${ROOT_DIR}/custom_addons/automotive_parts/static/src/js/invoice_ingest_react_page.bundle.js"

npx --yes esbuild "${SRC}" \
  --bundle \
  --format=iife \
  --target=es2019 \
  --outfile="${OUT}" \
  --jsx-factory=React.createElement \
  --jsx-fragment=React.Fragment

echo "Built ${OUT}"
