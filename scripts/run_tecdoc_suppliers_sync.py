#!/usr/bin/env python3
"""Sync full TecDoc supplier catalog into Odoo.

Example:
  ./odoo-venv/bin/python scripts/run_tecdoc_suppliers_sync.py \
    --config odoo.conf \
    --db TecDocIntegration

Override key/host/base URL for this run only:
  ./odoo-venv/bin/python scripts/run_tecdoc_suppliers_sync.py \
    --config odoo.conf \
    --db TecDocIntegration \
    --api-key YOUR_RAPIDAPI_KEY \
    --api-host tecdoc-catalog.p.rapidapi.com \
    --base-url https://tecdoc-catalog.p.rapidapi.com
"""

from __future__ import annotations

import argparse
import json
import os
import sys


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ODOO_DIR = os.path.join(ROOT_DIR, "odoo")
if ODOO_DIR not in sys.path:
    sys.path.insert(0, ODOO_DIR)

import odoo  # noqa: E402
from odoo import SUPERUSER_ID, api  # noqa: E402
from odoo.tools import config as odoo_config  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync full TecDoc suppliers into tecdoc.supplier")
    parser.add_argument("--config", default="odoo.conf", help="Path to Odoo config file")
    parser.add_argument("--db", required=True, help="Database name")
    parser.add_argument("--api-id", type=int, default=0, help="Use this tecdoc.api record ID")
    parser.add_argument("--api-key", default="", help="RapidAPI key override for this run only")
    parser.add_argument("--api-host", default="", help="RapidAPI host override for this run only")
    parser.add_argument("--base-url", default="", help="TecDoc base URL override for this run only")
    parser.add_argument(
        "--deactivate-missing",
        action="store_true",
        help="Set active=False for local suppliers not present in API response",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        help="Use TecDoc cache when fetching supplier list (default: disabled for fresh sync)",
    )
    parser.add_argument("--json", action="store_true", help="Print final result as JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    odoo_config.parse_config(["-c", args.config, "-d", args.db])
    odoo.service.server.load_server_wide_modules()
    registry = odoo.modules.registry.Registry(args.db)

    with registry.cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {})

        if "tecdoc.api" not in env or "tecdoc.supplier" not in env:
            print(
                "ERROR: required models missing. Install/upgrade automotive_parts first.",
                file=sys.stderr,
            )
            return 2

        api_model = env["tecdoc.api"].sudo()
        if args.api_id:
            api_rec = api_model.browse(args.api_id).exists()
        else:
            api_rec = api_model.search([], order="id asc", limit=1)

        if not api_rec:
            if not args.api_key:
                print(
                    "ERROR: no tecdoc.api record found and no --api-key override provided.",
                    file=sys.stderr,
                )
                return 2
            api_rec = api_model.create(
                {
                    "name": "TecDoc API (CLI Suppliers Sync)",
                    "api_key": args.api_key,
                    "api_host": args.api_host or "tecdoc-catalog.p.rapidapi.com",
                    "base_url": args.base_url or "https://tecdoc-catalog.p.rapidapi.com",
                }
            )
            cr.commit()

        run_ctx = {}
        if args.api_key:
            run_ctx["tecdoc_api_key_override"] = args.api_key
        if args.api_host:
            run_ctx["tecdoc_api_host_override"] = args.api_host
        if args.base_url:
            run_ctx["tecdoc_base_url_override"] = args.base_url

        api_run = api_rec.with_context(**run_ctx)
        result = api_run.sync_suppliers_catalog(
            deactivate_missing=args.deactivate_missing,
            use_cache=args.use_cache,
        )
        cr.commit()

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(
                "TecDoc suppliers sync complete: "
                f"received={result.get('total_received', 0)} "
                f"created={result.get('created', 0)} "
                f"updated={result.get('updated', 0)} "
                f"deactivated={result.get('deactivated', 0)} "
                f"active_total={result.get('active_total', 0)}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

