#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-}"

if [[ -z "${BASE_URL}" ]]; then
  echo "Usage: $0 <base-url>" >&2
  exit 1
fi

trimmed="${BASE_URL%/}"
health_url="${trimmed}/web/health"
login_url="${trimmed}/web/login"

echo "Smoke testing ${trimmed}"

curl --fail --silent --show-error \
  --retry 10 \
  --retry-all-errors \
  --retry-delay 3 \
  --max-time 15 \
  "${health_url}" >/dev/null

curl --fail --silent --show-error \
  --retry 5 \
  --retry-all-errors \
  --retry-delay 2 \
  --max-time 15 \
  "${login_url}" >/dev/null

echo "Smoke test passed for ${trimmed}"
