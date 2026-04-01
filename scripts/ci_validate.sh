#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[1/5] Python syntax"
mapfile -t PY_FILES < <(find custom_addons/automotive_parts -type f -name '*.py' | sort)
python3 -m py_compile "${PY_FILES[@]}"

echo "[2/5] XML parsing"
python3 - <<'PY'
from pathlib import Path
import xml.etree.ElementTree as ET

for path in sorted(Path("custom_addons/automotive_parts").rglob("*.xml")):
    ET.parse(path)
    print(f"OK {path}")
PY

echo "[3/5] Frontend bundle"
scripts/build_invoice_ingest_react.sh

echo "[4/5] Odoo regression tests"
scripts/run_odoo_tests.sh

echo "[5/5] Docker build"
docker build -t odoo-integration-ci .

echo "CI validation passed."
