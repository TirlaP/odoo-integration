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
ADDONS_PATH="${ODOO_ADDONS_PATH:-/app/odoo/addons,/app/custom_addons}"
LIST_DB="${ODOO_LIST_DB:-False}"
PROXY_MODE="${ODOO_PROXY_MODE:-True}"
ADMIN_PASSWD="${ODOO_ADMIN_PASSWD:-admin}"
WORKERS="${ODOO_WORKERS:-0}"
MAX_CRON_THREADS="${ODOO_MAX_CRON_THREADS:-1}"
DB_FILTER="${ODOO_DB_FILTER:-}"
CONF_FILE="${ODOO_CONF_FILE:-/tmp/odoo-railway.conf}"

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

db_exists() {
  local target_db="$1"
  python3 - <<'PY'
import os
import psycopg2
import sys

db_name = os.environ["__TARGET_DB__"]
host = os.environ["__DB_HOST__"]
port = int(os.environ.get("__DB_PORT__", "5432"))
user = os.environ["__DB_USER__"]
password = os.environ.get("__DB_PASSWORD__", "")
sslmode = os.environ.get("__DB_SSLMODE__", "prefer")

try:
    conn = psycopg2.connect(
        dbname=db_name,
        user=user,
        password=password,
        host=host,
        port=port,
        connect_timeout=5,
        sslmode=sslmode,
    )
    conn.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
PY
}

if [[ "${ODOO_INIT_DB:-}" == "1" || "${ODOO_INIT_DB:-}" == "true" ]]; then
  if [[ -z "${DB_NAME}" ]]; then
    echo "ODOO_INIT_DB is enabled but ODOO_DB_NAME/PGDATABASE is missing." >&2
    exit 1
  fi
  if ! __TARGET_DB__="${DB_NAME}" __DB_HOST__="${DB_HOST}" __DB_PORT__="${DB_PORT}" __DB_USER__="${DB_USER}" __DB_PASSWORD__="${DB_PASSWORD}" __DB_SSLMODE__="${DB_SSLMODE}" db_exists "${DB_NAME}"; then
    INIT_MODULES="${ODOO_INIT_MODULES:-base,web,automotive_parts}"
    echo "Database ${DB_NAME} not reachable; attempting initialization with modules: ${INIT_MODULES}"
    python3 /app/odoo/odoo-bin -c "${CONF_FILE}" -d "${DB_NAME}" -i "${INIT_MODULES}" --without-demo=all --stop-after-init --no-http --logfile -
  fi
fi

AUTO_UPDATE_MODULES="${ODOO_AUTO_UPDATE_MODULES:-true}"
if [[ "${AUTO_UPDATE_MODULES}" == "1" || "${AUTO_UPDATE_MODULES}" == "true" ]]; then
  if [[ -z "${DB_NAME}" ]]; then
    echo "ODOO_AUTO_UPDATE_MODULES is enabled but ODOO_DB_NAME/PGDATABASE is missing." >&2
    exit 1
  fi
  MODULES="${ODOO_UPDATE_MODULES:-automotive_parts}"
  echo "Running one-time module update: ${MODULES} on db=${DB_NAME}"
  python3 /app/odoo/odoo-bin -c "${CONF_FILE}" -d "${DB_NAME}" -u "${MODULES}" --stop-after-init --no-http --logfile -
fi

EXTRA_ARGS=()
if [[ -n "${DB_NAME}" ]]; then
  EXTRA_ARGS+=(-d "${DB_NAME}")
fi

exec python3 /app/odoo/odoo-bin \
  -c "${CONF_FILE}" \
  --logfile - \
  --workers "${WORKERS}" \
  --max-cron-threads "${MAX_CRON_THREADS}" \
  "${EXTRA_ARGS[@]}"
