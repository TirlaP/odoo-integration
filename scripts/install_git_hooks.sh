#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p .githooks
chmod +x .githooks/pre-commit scripts/pre_commit_validate.sh scripts/build_invoice_ingest_react.sh
git config core.hooksPath .githooks

echo "Git hooks installed."
echo "Configured core.hooksPath=.githooks"
