#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${VENV_DIR:-odoo-venv}"
PYTHON_BIN="${PYTHON_BIN:-${VENV_DIR}/bin/python3}"
ODOO_BIN="${ODOO_BIN:-odoo/odoo-bin}"
CONF_FILE="${CONF_FILE:-odoo.conf}"
MODULES="${MODULES:-automotive_parts}"
DB_NAME="${ODOO_TEST_DB:-automotive_parts_test_$(date +%s)}"
KEEP_DB="${KEEP_TEST_DB:-0}"
TEST_TAGS="${ODOO_TEST_TAGS:-/automotive_parts}"
TMP_CONF_FILE=""

parse_conf() {
  python3 - "$CONF_FILE" <<'PY'
import configparser
import sys

conf = configparser.ConfigParser()
conf.read(sys.argv[1])
opts = conf["options"]
for key, env in {
    "db_host": "PGHOST",
    "db_port": "PGPORT",
    "db_user": "PGUSER",
    "db_password": "PGPASSWORD",
}.items():
    value = opts.get(key, "").strip()
    if value and value.lower() != "false":
        print(f"{env}={value}")
PY
}

while IFS='=' read -r key value; do
  [[ -n "${key}" ]] || continue
  export "${key}=${value}"
done < <(parse_conf)

cleanup() {
  if [[ -n "${TMP_CONF_FILE}" ]]; then
    rm -f "${TMP_CONF_FILE}" >/dev/null 2>&1 || true
  fi
  if [[ "${KEEP_DB}" == "1" ]]; then
    echo "Keeping test database ${DB_NAME}"
    return
  fi
  dropdb --if-exists "${DB_NAME}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

TMP_CONF_FILE="$(mktemp -t odoo-test-conf.XXXXXX)"
python3 - "${CONF_FILE}" "${TMP_CONF_FILE}" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text()
target = Path(sys.argv[2])
lines = []
for line in source.splitlines():
    stripped = line.strip()
    if stripped.startswith('dev_mode'):
        continue
    if stripped.startswith('http_port'):
        lines.append('http_port = 0')
        continue
    lines.append(line)
target.write_text('\n'.join(lines) + '\n')
PY

dropdb --if-exists "${DB_NAME}" >/dev/null 2>&1 || true
createdb "${DB_NAME}"

echo "Installing and testing ${MODULES} on temp db ${DB_NAME}"
"${PYTHON_BIN}" "${ODOO_BIN}" \
  -c "${TMP_CONF_FILE}" \
  -d "${DB_NAME}" \
  -i "${MODULES}" \
  --test-enable \
  --test-tags "${TEST_TAGS}" \
  --stop-after-init \
  --no-http \
  --logfile -
