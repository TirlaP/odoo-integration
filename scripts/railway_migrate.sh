#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

parse_database_url() {
  python3 - <<'PY'
import os
import urllib.parse

url = os.environ.get("DATABASE_URL", "").strip()
if not url:
    raise SystemExit(0)

parsed = urllib.parse.urlparse(url)
if parsed.scheme not in {"postgres", "postgresql"}:
    raise SystemExit(0)

if parsed.hostname:
    print(f"PGHOST={parsed.hostname}")
if parsed.port:
    print(f"PGPORT={parsed.port}")
if parsed.username:
    print(f"PGUSER={urllib.parse.unquote(parsed.username)}")
if parsed.password:
    print(f"PGPASSWORD={urllib.parse.unquote(parsed.password)}")
db_name = parsed.path.lstrip("/")
if db_name:
    print(f"PGDATABASE={urllib.parse.unquote(db_name)}")
query = urllib.parse.parse_qs(parsed.query)
sslmode = query.get("sslmode", [None])[0]
if sslmode:
    print(f"PGSSLMODE={sslmode}")
PY
}

if [[ -n "${DATABASE_URL:-}" ]]; then
  while IFS='=' read -r key value; do
    [[ -n "${key}" ]] || continue
    export "${key}=${value}"
  done < <(parse_database_url)
fi

DB_HOST="${ODOO_DB_HOST:-${PGHOST:-localhost}}"
DB_PORT="${ODOO_DB_PORT:-${PGPORT:-5432}}"
DB_USER="${ODOO_DB_USER:-${PGUSER:-odoo}}"
DB_PASSWORD="${ODOO_DB_PASSWORD:-${PGPASSWORD:-odoo}}"
DB_NAME="${ODOO_DB_NAME:-${PGDATABASE:-}}"
DB_SSLMODE="${ODOO_DB_SSLMODE:-${PGSSLMODE:-prefer}}"

PORT="${PORT:-${ODOO_HTTP_PORT:-8069}}"
DATA_DIR="${ODOO_DATA_DIR:-/data}"
ADDONS_PATH="${ODOO_ADDONS_PATH:-${ROOT_DIR}/odoo/addons,${ROOT_DIR}/custom_addons}"
LIST_DB="${ODOO_LIST_DB:-False}"
PROXY_MODE="${ODOO_PROXY_MODE:-True}"
ADMIN_PASSWD="${ODOO_ADMIN_PASSWD:-admin}"
DB_FILTER="${ODOO_DB_FILTER:-}"
CONF_FILE="${ODOO_CONF_FILE:-/tmp/odoo-railway.conf}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ODOO_BIN="${ODOO_BIN:-${ROOT_DIR}/odoo/odoo-bin}"
MODULES="${ODOO_UPDATE_MODULES:-automotive_parts}"

if [[ -z "${DB_NAME}" ]]; then
  echo "ODOO_DB_NAME/PGDATABASE is required for railway_migrate.sh" >&2
  exit 1
fi

mkdir -p "${DATA_DIR}"

cat > "${CONF_FILE}" <<EOF
[options]
addons_path = ${ADDONS_PATH}
data_dir = ${DATA_DIR}
db_host = ${DB_HOST}
db_port = ${DB_PORT}
db_user = ${DB_USER}
db_password = ${DB_PASSWORD}
db_sslmode = ${DB_SSLMODE}
http_port = ${PORT}
proxy_mode = ${PROXY_MODE}
list_db = ${LIST_DB}
admin_passwd = ${ADMIN_PASSWD}
EOF

if [[ -n "${DB_FILTER}" ]]; then
  echo "dbfilter = ${DB_FILTER}" >> "${CONF_FILE}"
fi

echo "Running Odoo module upgrade: ${MODULES} on db=${DB_NAME}"
exec "${PYTHON_BIN}" "${ODOO_BIN}" \
  -c "${CONF_FILE}" \
  -d "${DB_NAME}" \
  -u "${MODULES}" \
  --stop-after-init \
  --no-http \
  --logfile -
