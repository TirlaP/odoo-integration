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
TMP_DATA_DIR=""

conf_has_options() {
  [[ -f "${CONF_FILE}" ]] || return 1
  python3 - "${CONF_FILE}" <<'PY'
import configparser
import sys

conf = configparser.ConfigParser()
conf.read(sys.argv[1])
raise SystemExit(0 if conf.has_section("options") else 1)
PY
}

conf_value() {
  local key="$1"
  local fallback="${2:-}"
  if ! conf_has_options; then
    printf '%s\n' "${fallback}"
    return
  fi
  python3 - "${CONF_FILE}" "${key}" "${fallback}" <<'PY'
import configparser
import sys

conf = configparser.ConfigParser()
conf.read(sys.argv[1])
value = conf["options"].get(sys.argv[2], sys.argv[3]).strip()
print(value)
PY
}

parse_conf() {
  conf_has_options || return 0
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
  if [[ -n "${TMP_DATA_DIR}" && "${KEEP_DB}" != "1" ]]; then
    rm -rf "${TMP_DATA_DIR}" >/dev/null 2>&1 || true
  fi
  if [[ "${KEEP_DB}" == "1" ]]; then
    echo "Keeping test database ${DB_NAME}"
    [[ -z "${TMP_DATA_DIR}" ]] || echo "Keeping test data dir ${TMP_DATA_DIR}"
    return
  fi
  dropdb --if-exists "${DB_NAME}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if [[ ! -x "${ODOO_BIN}" ]]; then
  echo "Odoo binary not found at ${ODOO_BIN}. Did you checkout the odoo submodule?" >&2
  exit 1
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python runtime not found at ${PYTHON_BIN}. Create the venv or set PYTHON_BIN." >&2
  exit 1
fi

TMP_CONF_FILE="$(mktemp -t odoo-test-conf.XXXXXX)"
TMP_DATA_DIR="$(mktemp -d -t odoo-test-data.XXXXXX)"

ADDONS_PATH="${ODOO_ADDONS_PATH:-$(conf_value addons_path "${ROOT_DIR}/odoo/addons,${ROOT_DIR}/custom_addons")}"
DATA_DIR="${ODOO_DATA_DIR:-${TMP_DATA_DIR}}"
DB_HOST="${ODOO_DB_HOST:-${PGHOST:-$(conf_value db_host localhost)}}"
DB_PORT="${ODOO_DB_PORT:-${PGPORT:-$(conf_value db_port 5432)}}"
DB_USER="${ODOO_DB_USER:-${PGUSER:-$(conf_value db_user odoo)}}"
DB_PASSWORD="${ODOO_DB_PASSWORD:-${PGPASSWORD:-$(conf_value db_password False)}}"
ADMIN_PASSWD="${ODOO_ADMIN_PASSWD:-$(conf_value admin_passwd admin)}"

export PGHOST="${DB_HOST}"
export PGPORT="${DB_PORT}"
export PGUSER="${DB_USER}"
if [[ "${DB_PASSWORD,,}" == "false" ]]; then
  unset PGPASSWORD
else
  export PGPASSWORD="${DB_PASSWORD}"
fi

python3 - "${TMP_CONF_FILE}" "${ADDONS_PATH}" "${DATA_DIR}" "${DB_HOST}" "${DB_PORT}" "${DB_USER}" "${DB_PASSWORD}" "${ADMIN_PASSWD}" <<'PY'
from pathlib import Path
import sys

target = Path(sys.argv[1])
addons_path, data_dir, db_host, db_port, db_user, db_password, admin_passwd = sys.argv[2:]
target.write_text(
    "\n".join(
        [
            "[options]",
            f"addons_path = {addons_path}",
            f"data_dir = {data_dir}",
            f"db_host = {db_host}",
            f"db_port = {db_port}",
            f"db_user = {db_user}",
            f"db_password = {db_password}",
            "http_port = 0",
            f"admin_passwd = {admin_passwd}",
            "log_level = info",
            "",
        ]
    ),
    encoding="utf-8",
)
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
