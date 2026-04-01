#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[pre-commit 1/3] Python syntax"
mapfile -t PY_FILES < <(find custom_addons/automotive_parts -type f -name '*.py' | sort)
python3 -m py_compile "${PY_FILES[@]}"

echo "[pre-commit 2/3] XML parsing"
python3 - <<'PY'
from pathlib import Path
import xml.etree.ElementTree as ET

for path in sorted(Path("custom_addons/automotive_parts").rglob("*.xml")):
    ET.parse(path)
    print(f"OK {path}")
PY

echo "[pre-commit 3/3] Frontend bundle"
scripts/build_invoice_ingest_react.sh

echo "Pre-commit validation passed."
