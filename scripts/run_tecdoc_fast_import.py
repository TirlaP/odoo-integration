#!/usr/bin/env python3
"""Run/resume TecDoc fast import from JSON files into Odoo.

Example:
  ./odoo-venv/bin/python scripts/run_tecdoc_fast_import.py \
    --config odoo.conf \
    --db TecDocIntegration \
    --directory /Users/petruinstagram/Desktop/web-apps/odoo-integration/tecdoc_data/by_code \
    --batch-size 200
"""

from __future__ import annotations

import argparse
import os
import sys
import time


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ODOO_DIR = os.path.join(ROOT_DIR, "odoo")
if ODOO_DIR not in sys.path:
    sys.path.insert(0, ODOO_DIR)

import odoo  # noqa: E402
from odoo import SUPERUSER_ID, api  # noqa: E402
from odoo.tools import config as odoo_config  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run/resume TecDoc fast import run")
    parser.add_argument("--config", default="odoo.conf", help="Path to Odoo config file")
    parser.add_argument("--db", required=True, help="Database name")
    parser.add_argument("--directory", required=True, help="Directory with TecDoc JSON export (prefer .../by_code)")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--run-mode", choices=["full", "xrefs_only"], default="full")
    parser.add_argument("--replace-variant-details", action="store_true", default=True)
    parser.add_argument("--no-replace-variant-details", dest="replace_variant_details", action="store_false")
    parser.add_argument("--mark-products-managed", action="store_true", default=True)
    parser.add_argument("--no-mark-products-managed", dest="mark_products_managed", action="store_false")
    parser.add_argument("--import-cross-references", action="store_true", default=True)
    parser.add_argument("--no-import-cross-references", dest="import_cross_references", action="store_false")
    parser.add_argument("--resume-latest", action="store_true", default=True, help="Resume latest run if possible")
    parser.add_argument("--no-resume-latest", dest="resume_latest", action="store_false")
    parser.add_argument(
        "--pause-import-cron",
        action="store_true",
        default=True,
        help="Temporarily disable TecDoc Fast Import cron while this script runs (recommended).",
    )
    parser.add_argument("--no-pause-import-cron", dest="pause_import_cron", action="store_false")
    parser.add_argument("--print-every", type=int, default=20, help="Print progress every N batches")
    parser.add_argument("--sleep", type=float, default=0.0, help="Sleep between batches in seconds")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    odoo_config.parse_config(["-c", args.config, "-d", args.db])
    odoo.service.server.load_server_wide_modules()
    registry = odoo.modules.registry.Registry(args.db)

    with registry.cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {})
        cron = None
        cron_prev_active = None

        try:
            # Make sure model exists in this DB.
            if "tecdoc.fast.import.run" not in env:
                print(
                    "ERROR: model tecdoc.fast.import.run not found in this DB. "
                    "Install/upgrade automotive_parts first.",
                    file=sys.stderr,
                )
                return 2

            run_model = env["tecdoc.fast.import.run"].sudo()

            if args.pause_import_cron:
                cron = env.ref("automotive_parts.ir_cron_tecdoc_fast_import", raise_if_not_found=False)
                if cron:
                    cron_prev_active = bool(cron.active)
                    if cron_prev_active:
                        cron.sudo().write({"active": False})
                        cr.commit()
                        print(f"Paused cron {cron.id} during manual import")

            run = None
            if args.resume_latest:
                run = run_model.search([], order="id desc", limit=1)
                if run and run.directory != args.directory:
                    run = None

            if not run:
                run = run_model.create(
                    {
                        "name": f"Bulk import from {args.directory}",
                        "directory": args.directory,
                        "batch_size": args.batch_size,
                        "run_mode": args.run_mode,
                        "replace_variant_details": args.replace_variant_details,
                        "mark_products_managed": args.mark_products_managed,
                        "import_cross_references": args.import_cross_references,
                    }
                )
                cr.commit()

            if run.state in ("draft", "failed"):
                run.action_start()
                cr.commit()

            print(f"Run ID: {run.id} | state={run.state} | directory={run.directory}")

            loops = 0
            started = time.time()
            while run.state == "running":
                run._process_batch()
                cr.commit()
                run = run_model.browse(run.id).sudo()
                loops += 1

                if loops % max(1, args.print_every) == 0:
                    elapsed = time.time() - started
                    print(
                        f"progress loops={loops} elapsed={elapsed:.1f}s "
                        f"cursor={run.cursor} processed={run.processed} "
                        f"created_products={run.created_products} "
                        f"created_variants={run.created_variants} "
                        f"updated_variants={run.updated_variants} state={run.state}"
                    )

                if args.sleep > 0:
                    time.sleep(args.sleep)

            print(
                f"FINAL state={run.state} processed={run.processed} "
                f"created_products={run.created_products} created_variants={run.created_variants} "
                f"updated_variants={run.updated_variants}"
            )
            if run.state == "failed":
                print(f"ERROR: {run.last_error}", file=sys.stderr)
                return 1
        finally:
            if cron and cron_prev_active:
                cron.sudo().write({"active": True})
                cr.commit()
                print(f"Resumed cron {cron.id}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
