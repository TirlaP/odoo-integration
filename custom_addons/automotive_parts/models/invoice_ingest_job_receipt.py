# -*- coding: utf-8 -*-
from collections import defaultdict

from odoo import fields, models
from odoo.exceptions import UserError


class InvoiceIngestJobReceipt(models.Model):
    _inherit = 'invoice.ingest.job'
    def _get_default_incoming_picking_type(self):
        self.ensure_one()
        company = self.env.company
        PickingType = self.env['stock.picking.type']
        picking_type = PickingType.search(
            [('code', '=', 'incoming'), ('warehouse_id.company_id', '=', company.id)],
            order='sequence, id',
            limit=1,
        )
        if not picking_type:
            picking_type = PickingType.search(
                [('code', '=', 'incoming'), ('company_id', '=', company.id)],
                order='sequence, id',
                limit=1,
            )
        if not picking_type:
            raise UserError('No incoming picking type found. Configure Inventory receipts first.')
        return picking_type

    def _collect_receipt_quantities(self):
        self.ensure_one()
        quantities = defaultdict(float)
        unmatched_count = 0
        for line in self.line_ids.sorted('sequence'):
            qty = self._safe_float(line.quantity, default=0.0)
            if qty <= 0:
                continue
            if not line.product_id:
                unmatched_count += 1
                continue
            quantities[line.product_id.id] += qty
        return dict(quantities), unmatched_count

    def _ensure_receipt(self, supplier):
        self.ensure_one()
        if not supplier:
            raise UserError('A supplier is required before reception synchronization can continue.')
        if not self.invoice_number:
            raise UserError('Invoice number is required before reception synchronization can continue.')

        normalized_invoice_number = self.env['stock.picking']._normalize_supplier_invoice_reference(self.invoice_number)
        if self.picking_id and self.picking_id.exists() and self.picking_id.state != 'cancel':
            picking = self.picking_id
            vals = {}
            if not picking.partner_id:
                vals['partner_id'] = supplier.id
            if self.invoice_number and not picking.origin:
                vals['origin'] = f'Invoice {self.invoice_number}'
            if self.invoice_number and not picking.supplier_invoice_number:
                vals['supplier_invoice_number'] = self.invoice_number
            if self.invoice_date and not picking.supplier_invoice_date:
                vals['supplier_invoice_date'] = self.invoice_date
            if vals:
                picking.with_context(skip_audit_log=True).write(vals)
            return picking, False

        domain = [
            ('picking_type_code', '=', 'incoming'),
            ('partner_id', '=', supplier.id),
            ('supplier_invoice_number', '!=', False),
            ('state', '!=', 'cancel'),
        ]
        if self.invoice_date:
            domain.append(('supplier_invoice_date', '=', self.invoice_date))
        existing = self.env['stock.picking'].search(domain, order='id desc')
        existing = existing.filtered(
            lambda picking: self.env['stock.picking']._normalize_supplier_invoice_reference(picking.supplier_invoice_number)
            == normalized_invoice_number
        )[:1]
        if existing:
            self.picking_id = existing.id
            vals = {}
            if self.invoice_date and not existing.supplier_invoice_date:
                vals['supplier_invoice_date'] = self.invoice_date
            if vals:
                existing.with_context(skip_audit_log=True).write(vals)
            return existing, False

        picking_type = self._get_default_incoming_picking_type()
        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'partner_id': supplier.id,
            'origin': f'Invoice {self.invoice_number}' if self.invoice_number else self.name,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
            'supplier_invoice_number': self.invoice_number,
            'supplier_invoice_date': self.invoice_date,
        })
        self.picking_id = picking.id
        return picking, True

    def _sync_receipt_moves(self, picking, product_quantities):
        self.ensure_one()
        if picking.state in {'done', 'cancel'}:
            return 0

        Move = self.env['stock.move']
        MoveLine = self.env['stock.move.line']
        SaleOrderLine = self.env['sale.order.line']
        updated = 0
        for product_id, qty in product_quantities.items():
            if qty <= 0:
                continue
            product = self.env['product.product'].browse(product_id).exists()
            if not product:
                continue

            remaining_qty = qty
            target_lines = SaleOrderLine.search([
                ('state', '=', 'sale'),
                ('product_id', '=', product.id),
                ('order_id.auto_state', 'not in', ['cancel', 'delivered']),
                ('company_id', '=', picking.company_id.id),
            ]).sorted(lambda line: (line.order_id.date_order or fields.Datetime.now(), line.id))

            for sale_line in target_lines:
                if remaining_qty <= 0:
                    break
                line_needed_qty = max(sale_line.product_uom_qty - sale_line._get_ready_qty(), 0.0)
                line_needed_qty = sale_line.product_uom._compute_quantity(
                    line_needed_qty,
                    product.uom_id,
                    rounding_method='HALF-UP',
                )
                if line_needed_qty <= 0:
                    continue

                target_moves = sale_line._get_supply_target_moves(picking.location_dest_id)
                for target_move in target_moves:
                    if remaining_qty <= 0 or line_needed_qty <= 0:
                        break

                    existing_supply_qty = 0.0
                    for origin_move in target_move.move_orig_ids.filtered(
                        lambda move: move.state not in {'cancel', 'done'}
                        and move.product_id == product
                        and move.location_dest_id == target_move.location_id
                        and move.picking_id != picking
                    ):
                        existing_supply_qty += origin_move.product_uom._compute_quantity(
                            origin_move.product_uom_qty,
                            target_move.product_uom,
                            rounding_method='HALF-UP',
                        )

                    reserved_qty = target_move.quantity
                    covered_qty = reserved_qty + existing_supply_qty
                    needed_qty = max(target_move.product_uom_qty - covered_qty, 0.0)
                    needed_qty = target_move.product_uom._compute_quantity(
                        needed_qty,
                        product.uom_id,
                        rounding_method='HALF-UP',
                    )
                    if needed_qty <= 0:
                        continue

                    allocated_qty = min(remaining_qty, line_needed_qty, needed_qty)
                    linked_sale_lines = target_move._get_sale_order_lines()
                    move = picking.move_ids_without_package.filtered(
                        lambda move: move.product_id == product
                        and move.state not in {'done', 'cancel'}
                        and target_move in move.move_dest_ids
                    )[:1]
                    move_vals = {
                        'product_uom_qty': allocated_qty,
                        'quantity': allocated_qty,
                        'product_uom': product.uom_id.id,
                        'move_dest_ids': [(6, 0, [target_move.id])],
                    }
                    if len(linked_sale_lines) == 1:
                        move_vals['sale_line_id'] = linked_sale_lines.id
                        move_vals['group_id'] = linked_sale_lines.order_id.procurement_group_id.id
                    if move:
                        move.write(move_vals)
                    else:
                        move_vals.update({
                            'name': product.display_name,
                            'product_id': product.id,
                            'picking_id': picking.id,
                            'location_id': picking.location_id.id,
                            'location_dest_id': picking.location_dest_id.id,
                        })
                        move = Move.create(move_vals)
                    if move.state == 'draft':
                        move._action_confirm()

                    move_line = move.move_line_ids.filtered(
                        lambda line: line.product_id.id == product.id
                        and line.location_id.id == picking.location_id.id
                        and line.location_dest_id.id == picking.location_dest_id.id
                        and not line.lot_id
                    )[:1]
                    if move_line:
                        move_line.write({
                            'product_uom_id': product.uom_id.id,
                            'quantity': allocated_qty,
                        })
                        extra_lines = (move.move_line_ids - move_line).filtered(
                            lambda line: line.product_id.id == product.id and not line.lot_id and line.state != 'done'
                        )
                        if extra_lines:
                            extra_lines.unlink()
                    else:
                        MoveLine.create({
                            'picking_id': picking.id,
                            'move_id': move.id,
                            'product_id': product.id,
                            'product_uom_id': product.uom_id.id,
                            'location_id': picking.location_id.id,
                            'location_dest_id': picking.location_dest_id.id,
                            'quantity': allocated_qty,
                        })
                    updated += 1
                    remaining_qty -= allocated_qty
                    line_needed_qty -= allocated_qty

            if remaining_qty > 0:
                move = picking.move_ids_without_package.filtered(
                    lambda m: m.product_id.id == product.id
                    and m.state not in {'done', 'cancel'}
                    and not m.move_dest_ids
                )[:1]
                if move:
                    move.write({
                        'product_uom_qty': remaining_qty,
                        'quantity': remaining_qty,
                        'product_uom': product.uom_id.id,
                    })
                else:
                    move = Move.create({
                        'name': product.display_name,
                        'product_id': product.id,
                        'product_uom_qty': remaining_qty,
                        'quantity': remaining_qty,
                        'product_uom': product.uom_id.id,
                        'picking_id': picking.id,
                        'location_id': picking.location_id.id,
                        'location_dest_id': picking.location_dest_id.id,
                    })
                if move.state == 'draft':
                    move._action_confirm()

                move_line = move.move_line_ids.filtered(
                    lambda l: l.product_id.id == product.id
                    and l.location_id.id == picking.location_id.id
                    and l.location_dest_id.id == picking.location_dest_id.id
                    and not l.lot_id
                )[:1]
                if move_line:
                    move_line.write({
                        'product_uom_id': product.uom_id.id,
                        'quantity': remaining_qty,
                    })
                    extra_lines = (move.move_line_ids - move_line).filtered(
                        lambda l: l.product_id.id == product.id and not l.lot_id and l.state != 'done'
                    )
                    if extra_lines:
                        extra_lines.unlink()
                else:
                    MoveLine.create({
                        'picking_id': picking.id,
                        'move_id': move.id,
                        'product_id': product.id,
                        'product_uom_id': product.uom_id.id,
                        'location_id': picking.location_id.id,
                        'location_dest_id': picking.location_dest_id.id,
                        'quantity': remaining_qty,
                    })
                updated += 1

        affected_orders = self.env['sale.order']
        for move in picking.move_ids_without_package:
            affected_orders |= move._get_sale_order_lines().mapped('order_id')
            affected_orders |= move.sale_line_id.order_id
        if affected_orders:
            affected_orders._refresh_automotive_stock_state()

        return updated

    def _validate_receipt(self, picking):
        self.ensure_one()
        if not picking or picking.state in {'done', 'cancel'}:
            return bool(picking and picking.state == 'done')

        if picking.state == 'draft':
            picking.action_confirm()
        result = picking.button_validate()
        if isinstance(result, dict) and result.get('res_model') == 'stock.backorder.confirmation' and result.get('res_id'):
            self.env['stock.backorder.confirmation'].browse(result['res_id']).process()
        return picking.state == 'done'

    def _auto_create_or_update_receipt(self, supplier):
        self.ensure_one()
        if self._infer_vendor_bill_move_type() == 'in_refund':
            return {
                'created': False,
                'updated_lines': 0,
                'validated': False,
                'unmatched_count': 0,
                'reason': 'credit_note',
            }
        product_quantities, unmatched_count = self._collect_receipt_quantities()
        if not product_quantities:
            return {
                'created': False,
                'updated_lines': 0,
                'validated': False,
                'unmatched_count': unmatched_count,
                'reason': 'no_matched_products',
            }

        picking, created = self._ensure_receipt(supplier=supplier)
        updated_lines = self._sync_receipt_moves(picking, product_quantities)
        validated = False
        reason = ''
        if unmatched_count:
            reason = 'unmatched_lines'
        else:
            validated = self._validate_receipt(picking)
        return {
            'created': created,
            'updated_lines': updated_lines,
            'validated': validated,
            'unmatched_count': unmatched_count,
            'reason': reason,
        }

    def _prepare_vendor_bill_line_vals(self, description, quantity, price_unit, product_id=False):
        self.ensure_one()
        vals = {
            'name': description or 'Imported invoice line',
            'quantity': quantity or 1.0,
            'price_unit': price_unit or 0.0,
        }
        if product_id:
            vals['product_id'] = product_id
        return vals

    def _prepare_draft_vendor_bill_lines(self):
        self.ensure_one()
        line_vals = []
        if self.line_ids:
            for line in self.line_ids.sorted('sequence'):
                description = (
                    (line.product_description or '').strip()
                    or (line.product_code or '').strip()
                    or 'Imported invoice line'
                )
                line_vals.append((0, 0, self._prepare_vendor_bill_line_vals(
                    description=description,
                    quantity=line.quantity or 1.0,
                    price_unit=line.discounted_unit_price or line.unit_price or 0.0,
                    product_id=line.product_id.id if line.product_id else False,
                )))
        else:
            parsed_lines = self._get_normalized_invoice_payload().get('invoice_lines', [])
            for line in parsed_lines:
                if not isinstance(line, dict):
                    continue
                description = (
                    (line.get('product_description') or '').strip()
                    or (line.get('product_code') or '').strip()
                    or 'Imported invoice line'
                )
                line_vals.append((0, 0, self._prepare_vendor_bill_line_vals(
                    description=description,
                    quantity=self._safe_float(line.get('quantity'), default=1.0) or 1.0,
                    price_unit=self._safe_float(line.get('unit_price'), default=0.0),
                    product_id=line.get('matched_product_id') or False,
                )))

        if line_vals:
            return line_vals
        return [
            (0, 0, self._prepare_vendor_bill_line_vals(
                description='Imported invoice (needs review)',
                quantity=1,
                price_unit=self.amount_total or 0.0,
            )),
        ]

    def _ensure_draft_vendor_bill(self, supplier, move_type=None, payload=None):
        self.ensure_one()
        if not supplier:
            normalized = self._get_normalized_invoice_payload()
            hinted_name = (normalized.get('supplier_name') or '').strip()
            hint = f' Extracted invoice supplier hint: {hinted_name}.' if hinted_name else ''
            raise UserError(
                'Select the invoice supplier first (the vendor who issued the invoice, '
                'not the per-line product brand).'
                f'{hint}'
            )
        if not self.invoice_number:
            raise UserError('Set invoice number first.')

        payload = payload or self._get_payload_dict()
        move_type = move_type or self._infer_vendor_bill_move_type(payload=payload)
        bill_origin = 'existing_linked'

        if self.account_move_id:
            move = self.account_move_id
            if move.move_type != move_type:
                if move.state != 'draft':
                    raise UserError(
                        'The linked vendor bill is already posted and its type does not match the imported document.'
                    )
                move.write({'move_type': move_type})
            return move, bill_origin

        existing_move = self.env['account.move'].search(
            [
                ('move_type', '=', move_type),
                ('partner_id', '=', supplier.id),
                ('ref', '=', self.invoice_number),
                ('state', '!=', 'cancel'),
            ],
            order='id desc',
            limit=1,
        )
        if existing_move:
            self.write({'account_move_id': existing_move.id})
            return existing_move, 'reused_existing'

        move = self.env['account.move'].create({
            'move_type': move_type,
            'partner_id': supplier.id,
            'ref': self.invoice_number,
            'invoice_date': self.invoice_date,
            'invoice_line_ids': self._prepare_draft_vendor_bill_lines(),
        })
        self.write({'account_move_id': move.id})
        return move, 'created'

    def _raise_if_duplicate_job(self, description, user_message):
        self.ensure_one()
        duplicate_of = self._get_duplicate_of_job_id()
        if not duplicate_of:
            return
        duplicate = self.browse(duplicate_of).exists()
        self._audit_blocked_action(
            description=description,
            reason='duplicate_job',
            old_values={
                'duplicate_of_job_id': duplicate.id if duplicate else duplicate_of,
                'duplicate_of_name': duplicate.display_name if duplicate else False,
            },
        )
        raise UserError(user_message)

    def _receipt_info_for_move_type(self, supplier, move_type):
        self.ensure_one()
        if move_type == 'in_refund':
            return {
                'created': False,
                'updated_lines': 0,
                'validated': False,
                'reason': 'credit_note',
            }
        return self._auto_create_or_update_receipt(supplier=supplier)

    def _link_receipt_to_vendor_bill(self, move, move_type):
        self.ensure_one()
        if not move or not self.picking_id or move_type == 'in_refund':
            return
        self.picking_id.with_context(skip_audit_log=True).write({
            'supplier_invoice_id': move.id,
            'supplier_invoice_number': self.invoice_number,
            'supplier_invoice_date': self.invoice_date,
        })

    def _execute_bill_receipt_flow(self, supplier=None, payload=None):
        self.ensure_one()
        effective_supplier = supplier or self._resolve_supplier_for_billing() or self.partner_id
        effective_payload = payload if isinstance(payload, dict) else self._get_payload_dict()
        move_type = self._infer_vendor_bill_move_type(payload=effective_payload)
        move, bill_origin = self._ensure_draft_vendor_bill(
            supplier=effective_supplier,
            move_type=move_type,
            payload=effective_payload,
        )
        receipt_info = self._receipt_info_for_move_type(
            supplier=effective_supplier,
            move_type=move_type,
        )
        self._link_receipt_to_vendor_bill(move=move, move_type=move_type)
        return {
            'supplier': effective_supplier,
            'move': move,
            'move_type': move_type,
            'bill_origin': bill_origin,
            'receipt_info': receipt_info,
        }

    def _format_bill_receipt_notification(self, receipt_info, draft_bill=False):
        self.ensure_one()
        reference = self.invoice_number or self.id
        ready_message = 'draft bill ready' if draft_bill else 'bill ready'
        if receipt_info.get('reason') == 'credit_note':
            if draft_bill:
                return f"{reference}: draft bill ready; receipt sync skipped for credit note / refund."
            return f"{reference}: credit note / refund bill created; receipt sync skipped."
        if receipt_info.get('reason') == 'no_matched_products':
            if draft_bill:
                return f"{reference}: draft bill ready, but receipt skipped (no matched products)."
            return f"{reference}: bill created, but receipt skipped (no matched products)."
        if receipt_info.get('reason') == 'unmatched_lines':
            if draft_bill:
                return (
                    f"{reference}: draft bill ready; receipt updated, but unmatched lines remain; "
                    "validation left pending review."
                )
            return f"{reference}: bill ready; receipt has unmatched lines and was left open for review."
        return (
            f"{reference}: {ready_message}; receipt "
            f"{'created' if receipt_info.get('created') else 'updated'} "
            f"({receipt_info.get('updated_lines', 0)} lines), "
            f"validated={bool(receipt_info.get('validated'))}."
        )

    def _build_bill_receipt_audit_values(self, supplier, move, bill_origin, move_type, receipt_info):
        self.ensure_one()
        return {
            'account_move_id': move.id if move else False,
            'move_type': move_type,
            'bill_origin': bill_origin,
            'partner_id': supplier.id if supplier else False,
            'picking_id': self.picking_id.id if self.picking_id else False,
            'receipt_info': receipt_info,
            **self._audit_line_summary(),
        }

    def action_create_draft_vendor_bill(self):
        notifications = []
        for job in self:
            job._raise_if_duplicate_job(
                description=f'Invoice bill creation blocked for duplicate job: {job.display_name}',
                user_message='This ingest job is flagged as a duplicate. Resolve the original invoice before creating a bill.',
            )
            flow = job._execute_bill_receipt_flow(
                supplier=job._resolve_supplier_for_billing(),
                payload=job._get_payload_dict(),
            )
            notifications.append(job._format_bill_receipt_notification(
                flow['receipt_info'],
                draft_bill=False,
            ))
            job._audit_log(
                action='custom',
                description=f'Invoice ingest vendor bill prepared: {job.display_name}',
                new_values=job._build_bill_receipt_audit_values(
                    supplier=flow['supplier'],
                    move=flow['move'],
                    bill_origin=flow['bill_origin'],
                    move_type=flow['move_type'],
                    receipt_info=flow['receipt_info'],
                ),
            )
            job._sync_workflow_state(
                receipt_info=flow['receipt_info'],
                move_type=flow['move_type'],
            )

        if len(self) == 1 and notifications:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Invoice Import',
                    'message': notifications[0],
                    'type': 'success',
                    'sticky': False,
                    'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
                },
            }
        return True

    def action_sync_receipt_stock(self):
        notifications = []
        for job in self:
            job._raise_if_duplicate_job(
                description=f'Invoice receipt sync blocked for duplicate job: {job.display_name}',
                user_message='This ingest job is flagged as a duplicate. Resolve the original invoice before syncing receipt stock.',
            )
            flow = job._execute_bill_receipt_flow(
                supplier=job._resolve_supplier_for_billing() or job.partner_id,
            )
            notifications.append(job._format_bill_receipt_notification(
                flow['receipt_info'],
                draft_bill=True,
            ))
            job._audit_log(
                action='custom',
                description=f'Invoice ingest receipt sync executed: {job.display_name}',
                new_values=job._build_bill_receipt_audit_values(
                    supplier=flow['supplier'],
                    move=flow['move'],
                    bill_origin=flow['bill_origin'],
                    move_type=flow['move_type'],
                    receipt_info=flow['receipt_info'],
                ),
            )
            job._sync_workflow_state(
                receipt_info=flow['receipt_info'],
                move_type=flow['move_type'],
            )

        if len(self) == 1 and notifications:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Receipt + Bill Sync',
                    'message': notifications[0],
                    'type': 'success',
                    'sticky': False,
                    'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
                },
            }
        return True
