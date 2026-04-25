#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${VENV_DIR:-odoo-venv}"
PYTHON_BIN="${PYTHON_BIN:-${VENV_DIR}/bin/python3}"

echo "[1/6] Python syntax"
mapfile -t PY_FILES < <(find custom_addons/automotive_parts -type f -name '*.py' | sort)
python3 -m py_compile "${PY_FILES[@]}"

echo "[2/6] XML parsing"
python3 - <<'PY'
from pathlib import Path
import xml.etree.ElementTree as ET

for path in sorted(Path("custom_addons/automotive_parts").rglob("*.xml")):
    ET.parse(path)
    print(f"OK {path}")
PY

echo "[3/6] Frontend bundle"
bash scripts/build_invoice_ingest_react.sh

echo "[4/6] Python dependencies"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  python3 -m venv "${VENV_DIR}"
  "${PYTHON_BIN}" -m pip install --upgrade pip setuptools wheel
  "${PYTHON_BIN}" -m pip install -r odoo/requirements.txt -r scripts/requirements.txt
else
  echo "Using existing Python runtime ${PYTHON_BIN}"
fi

echo "[5/6] Odoo regression tests"
bash scripts/run_odoo_tests.sh

echo "[6/6] Docker build"
docker build -t odoo-integration-ci .

echo "CI validation passed."
