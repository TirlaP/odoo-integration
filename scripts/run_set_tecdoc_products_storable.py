#!/usr/bin/env python3
"""Mark all TecDoc products as stock-tracked (storable).

Example:
  ./odoo-venv/bin/python scripts/run_set_tecdoc_products_storable.py \
    --config odoo.conf \
    --db TecDocIntegration

Optional category assignment for all TecDoc products:
  ./odoo-venv/bin/python scripts/run_set_tecdoc_products_storable.py \
    --config odoo.conf \
    --db TecDocIntegration \
    --category-path "All/Automotive Parts"
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ODOO_DIR = os.path.join(ROOT_DIR, "odoo")
if ODOO_DIR not in sys.path:
    sys.path.insert(0, ODOO_DIR)

import odoo  # noqa: E402
from odoo import SUPERUSER_ID, api  # noqa: E402
from odoo.tools import config as odoo_config  # noqa: E402
from odoo.tools.float_utils import float_compare  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Set TecDoc products as storable (Track Inventory).")
    parser.add_argument("--config", default="odoo.conf", help="Path to Odoo config file")
    parser.add_argument("--db", required=True, help="Database name")
    parser.add_argument("--batch-size", type=int, default=2000, help="Batch size for writes")
    parser.add_argument(
        "--category-path",
        default="",
        help='Optional category path to assign all TecDoc products, e.g. "All/Automotive Parts"',
    )
    parser.add_argument(
        "--skip-rebuild-quants",
        action="store_true",
        help="Skip rebuilding quants for products converted through SQL fallback",
    )
    return parser.parse_args()


def chunked(seq, size):
    for idx in range(0, len(seq), size):
        yield seq[idx: idx + size]


def ensure_category_path(env, category_path: str):
    names = [part.strip() for part in (category_path or "").split("/") if part.strip()]
    if not names:
        return False

    Category = env["product.category"].sudo().with_context(active_test=False)
    parent = Category.browse()
    for name in names:
        domain = [("name", "=", name), ("parent_id", "=", parent.id or False)]
        category = Category.search(domain, limit=1)
        if not category:
            vals = {"name": name}
            if parent:
                vals["parent_id"] = parent.id
            category = Category.create(vals)
        parent = category
    return parent


def get_templates_with_done_moves(cr, template_ids):
    if not template_ids:
        return set()
    cr.execute(
        """
        SELECT DISTINCT pp.product_tmpl_id
          FROM stock_move_line sml
          JOIN product_product pp ON pp.id = sml.product_id
         WHERE sml.state = 'done'
           AND pp.product_tmpl_id = ANY(%s)
        """,
        (list(template_ids),),
    )
    return {row[0] for row in cr.fetchall() if row and row[0]}


def rebuild_quants_from_done_moves(env, template_ids, batch_size=1000):
    if not template_ids:
        return 0

    Product = env["product.product"].sudo().with_context(active_test=False)
    MoveLine = env["stock.move.line"].sudo().with_context(active_test=False)
    Location = env["stock.location"].sudo().with_context(active_test=False)
    Quant = env["stock.quant"].sudo().with_context(inventory_mode=True)

    product_ids = Product.search([("product_tmpl_id", "in", list(template_ids))]).ids
    adjusted = 0

    for product_batch in chunked(product_ids, max(1, batch_size)):
        move_lines = MoveLine.search([
            ("state", "=", "done"),
            ("product_id", "in", product_batch),
        ])
        net_by_product_location = defaultdict(float)
        for ml in move_lines:
            qty = float(ml.quantity or 0.0)
            if not qty:
                continue
            if ml.location_dest_id.usage == "internal":
                net_by_product_location[(ml.product_id.id, ml.location_dest_id.id)] += qty
            if ml.location_id.usage == "internal":
                net_by_product_location[(ml.product_id.id, ml.location_id.id)] -= qty

        for (product_id, location_id), target_qty in net_by_product_location.items():
            product = Product.browse(product_id)
            if not product.exists() or product.tracking != "none":
                continue
            location = Location.browse(location_id)
            if not location.exists():
                continue
            current_qty = Quant._get_available_quantity(
                product, location, lot_id=None, package_id=None, owner_id=None, strict=True, allow_negative=True
            )
            if float_compare(target_qty, current_qty, precision_rounding=product.uom_id.rounding) == 0:
                continue
            delta = target_qty - current_qty
            Quant._update_available_quantity(product, location, quantity=delta)
            adjusted += 1

    return adjusted


def main() -> int:
    args = parse_args()

    odoo_config.parse_config(["-c", args.config, "-d", args.db])
    odoo.service.server.load_server_wide_modules()
    registry = odoo.modules.registry.Registry(args.db)

    with registry.cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {})
        Template = env["product.template"].sudo().with_context(active_test=False, skip_audit_log=True)

        domain_tecdoc = ["|", ("tecdoc_id", "!=", False), ("tecdoc_article_no", "!=", False)]
        tecdoc_templates = Template.search(domain_tecdoc)
        tecdoc_ids = tecdoc_templates.ids

        if not tecdoc_ids:
            print("No TecDoc products found.")
            return 0

        storable_updates = 0
        sql_forced_updates = 0
        category_updates = 0

        target_category = ensure_category_path(env, args.category_path)
        target_category_id = target_category.id if target_category else False

        # Split templates that can be updated safely through ORM vs templates already used in stock moves.
        storable_candidates = Template.search(
            domain_tecdoc + ["|", ("is_storable", "=", False), ("type", "!=", "consu")]
        )
        candidate_ids = set(storable_candidates.ids)
        used_ids = get_templates_with_done_moves(cr, candidate_ids)
        orm_ids = list(candidate_ids - used_ids)
        sql_ids = list(used_ids)

        for batch_ids in chunked(orm_ids, max(1, args.batch_size)):
            batch_domain = [("id", "in", batch_ids)]

            not_storable = Template.search(
                batch_domain + ["|", ("is_storable", "=", False), ("type", "!=", "consu")]
            )
            if not_storable:
                not_storable.write({"type": "consu", "is_storable": True})
                storable_updates += len(not_storable)

            if target_category_id:
                wrong_category = Template.search(
                    batch_domain + [("categ_id", "!=", target_category_id)]
                )
                if wrong_category:
                    wrong_category.write({"categ_id": target_category_id})
                    category_updates += len(wrong_category)

            cr.commit()

        if sql_ids:
            cr.execute(
                """
                UPDATE product_template
                   SET type = 'consu',
                       is_storable = TRUE,
                       write_uid = %s,
                       write_date = NOW()
                 WHERE id = ANY(%s)
                   AND (type != 'consu' OR COALESCE(is_storable, FALSE) = FALSE)
                """,
                (SUPERUSER_ID, sql_ids),
            )
            sql_forced_updates = cr.rowcount
            cr.commit()
            env.invalidate_all()

            if target_category_id:
                for batch_ids in chunked(sql_ids, max(1, args.batch_size)):
                    wrong_category = Template.search(
                        [("id", "in", batch_ids), ("categ_id", "!=", target_category_id)]
                    )
                    if wrong_category:
                        wrong_category.write({"categ_id": target_category_id})
                        category_updates += len(wrong_category)
                        cr.commit()

        quant_adjustments = 0
        if sql_forced_updates and not args.skip_rebuild_quants:
            quant_adjustments = rebuild_quants_from_done_moves(
                env=env,
                template_ids=sql_ids,
                batch_size=args.batch_size,
            )
            cr.commit()

        total = len(tecdoc_ids)
        print(
            "TecDoc storable sync complete: "
            f"total={total} "
            f"updated_storable={storable_updates} "
            f"updated_storable_sql={sql_forced_updates} "
            f"quant_adjustments={quant_adjustments} "
            f"updated_category={category_updates} "
            f"category={target_category.display_name if target_category else '-'}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
