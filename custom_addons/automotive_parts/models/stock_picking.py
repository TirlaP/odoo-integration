# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare, float_is_zero


class StockPicking(models.Model):
    """Extended Stock Picking for NIR (Nota de Intrare-Recepție)"""
    _inherit = 'stock.picking'

    _AUDIT_FIELDS = {
        'partner_id',
        'origin',
        'scheduled_date',
        'date_deadline',
        'picking_type_id',
        'location_id',
        'location_dest_id',
        'nir_number',
        'supplier_invoice_id',
        'supplier_invoice_number',
        'supplier_invoice_date',
        'reception_notes',
        'received_by',
        'state',
    }

    # NIR specific fields
    nir_number = fields.Char('Număr NIR', readonly=True, copy=False, index=True)
    supplier_invoice_id = fields.Many2one('account.move', 'Factură Furnizor')
    supplier_invoice_number = fields.Char('Nr. Factură Furnizor', index=True)
    supplier_invoice_date = fields.Date('Dată Factură', index=True)

    # Reception notes
    reception_notes = fields.Text('Observații Recepție')
    received_by = fields.Many2one('res.users', 'Recepționat De', default=lambda self: self.env.user)

    # Quantity differences
    has_differences = fields.Boolean('Are Diferențe', compute='_compute_has_differences')

    def _audit_snapshot(self, field_names):
        self.ensure_one()
        snapshot = {}
        for field_name in field_names:
            if field_name not in self._fields:
                continue
            value = self[field_name]
            if isinstance(value, models.BaseModel):
                snapshot[field_name] = value.ids
            else:
                snapshot[field_name] = value
        return snapshot

    def _audit_lines_summary(self):
        self.ensure_one()
        lines = []
        for move in self.move_ids_without_package:
            lines.append({
                'product_id': move.product_id.id,
                'product': move.product_id.display_name,
                'demand_qty': move.product_uom_qty,
                'done_qty': move.quantity,
                'uom': move.product_uom.name if move.product_uom else False,
            })
        return lines

    @api.depends(
        'move_ids_without_package',
        'move_ids_without_package.product_uom_qty',
        'move_ids_without_package.move_line_ids.quantity',
    )
    def _compute_has_differences(self):
        """Check if there are quantity differences"""
        for picking in self:
            has_diff = False

            for move in picking.move_ids_without_package:
                # Compare demand vs done quantity via move lines
                if float_compare(
                    move.quantity,
                    move.product_uom_qty,
                    precision_rounding=move.product_uom.rounding,
                ) != 0:
                    has_diff = True
                    break

            picking.has_differences = has_diff

    @api.model_create_multi
    def create(self, vals_list):
        """Generate NIR number on create"""
        pickings = super().create(vals_list)

        # Generate NIR number for incoming shipments
        for picking in pickings:
            if picking.picking_type_code == 'incoming' and not picking.nir_number:
                picking.nir_number = self.env['ir.sequence'].next_by_code('stock.picking.nir') or 'NIR/NEW'

        audit_log = self.env['automotive.audit.log']
        for picking, vals in zip(pickings, vals_list):
            tracked_fields = [f for f in vals.keys() if f in picking._AUDIT_FIELDS]
            if picking.picking_type_code == 'incoming':
                tracked_fields = list(set(tracked_fields + ['nir_number']))
            audit_log.log_change(
                action='create',
                record=picking,
                description=f'Created picking: {picking.name}',
                new_values=picking._audit_snapshot(tracked_fields),
            )

        return pickings

    def action_scan_barcode(self):
        """Scan barcode for product identification"""
        self.ensure_one()

        return {
            'name': 'Scanare Cod de Bare',
            'type': 'ir.actions.act_window',
            'res_model': 'stock.barcode.scan.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_picking_id': self.id}
        }

    def action_print_labels(self):
        """Print labels for received products"""
        self.ensure_one()

        # This will trigger label printing for all products in this reception
        labels_count = 0

        for move in self.move_ids_without_package:
            if move.product_id:
                # Generate label (placeholder - integrate with real printer)
                labels_count += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Labels Generated',
                'message': f'{labels_count} labels ready for printing',
                'type': 'success',
            }
        }

    def action_link_invoice(self):
        """Link supplier invoice from ANAF"""
        self.ensure_one()

        return {
            'name': 'Asociază Factură ANAF',
            'type': 'ir.actions.act_window',
            'res_model': 'anaf.invoice.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_picking_id': self.id}
        }

    def button_validate(self):
        """Override validate to update order statuses"""
        result = super(StockPicking, self).button_validate()

        # Update related sales orders
        for picking in self:
            if picking.sale_id:
                picking.sale_id._update_auto_state()

            self.env['automotive.audit.log'].log_change(
                action='custom',
                record=picking,
                description=f'Validated picking: {picking.name}',
                new_values={
                    'picking_type_code': picking.picking_type_code,
                    'state': picking.state,
                    'lines': picking._audit_lines_summary(),
                },
            )

        return result

    def action_assign(self):
        result = super().action_assign()
        for picking in self:
            if picking.sale_id:
                picking.sale_id._update_auto_state()
        return result

    def write(self, vals):
        context = dict(self.env.context or {})
        tracked_fields = [f for f in vals.keys() if f in self._AUDIT_FIELDS]
        old_by_id = {}
        if tracked_fields and context.get('skip_audit_log') is not True:
            old_by_id = {p.id: p._audit_snapshot(tracked_fields) for p in self}

        result = super().write(vals)

        if tracked_fields and context.get('skip_audit_log') is not True:
            audit_log = self.env['automotive.audit.log']
            for picking in self:
                audit_log.log_change(
                    action='write',
                    record=picking,
                    description=f'Modified picking: {picking.name}',
                    old_values=old_by_id.get(picking.id),
                    new_values=picking._audit_snapshot(tracked_fields),
                )

        return result


class StockBarcodeScanWizard(models.TransientModel):
    """Wizard for barcode scanning during reception"""
    _name = 'stock.barcode.scan.wizard'
    _description = 'Barcode Scan Wizard'

    picking_id = fields.Many2one('stock.picking', 'Reception', required=True)
    barcode = fields.Char('Barcode', required=True)
    product_id = fields.Many2one('product.product', 'Product', readonly=True)
    quantity = fields.Float('Quantity', default=1.0)
    create_product_if_missing = fields.Boolean('Create product if missing', default=False)
    new_product_name = fields.Char('New Product Name')
    new_default_code = fields.Char('New Internal Reference')
    barcode_target = fields.Selection(
        [('barcode', 'Barcode (EAN)'), ('barcode_internal', 'Internal Barcode')],
        default='barcode_internal',
        required=True,
    )

    @api.onchange('barcode')
    def _onchange_barcode(self):
        """Find product by barcode"""
        if self.barcode:
            digits = ''.join(ch for ch in (self.barcode or '') if ch.isdigit())
            if len(digits) in {8, 12, 13, 14}:
                self.barcode_target = 'barcode'
            else:
                self.barcode_target = 'barcode_internal'

            product = self.env['product.product'].search([
                '|', ('barcode', '=', self.barcode),
                ('barcode_internal', '=', self.barcode)
            ], limit=1)

            self.product_id = product.id if product else False

            if not product:
                if not self.new_default_code:
                    self.new_default_code = self.barcode
                return {
                    'warning': {
                        'title': 'Product Not Found',
                        'message': f'No product found with barcode: {self.barcode}'
                    }
                }

    def _create_product_from_scan(self):
        self.ensure_one()
        if not self.barcode:
            raise UserError('Scan a barcode first.')
        name = (self.new_product_name or '').strip() or f'New Product ({self.barcode})'

        template_vals = {
            'name': name,
            'type': 'consu',
            'is_storable': True,
        }
        if self.new_default_code:
            template_vals['default_code'] = (self.new_default_code or '').strip()

        if self.barcode_target == 'barcode':
            template_vals['barcode'] = self.barcode

        template = self.env['product.template'].create(template_vals)
        variant = template.product_variant_id
        if self.barcode_target == 'barcode_internal':
            variant.write({'barcode_internal': self.barcode})

        self.product_id = variant.id
        return variant

    def _get_or_create_move(self):
        self.ensure_one()
        picking = self.picking_id
        move = picking.move_ids_without_package.filtered(
            lambda m: m.product_id == self.product_id and m.state not in {'done', 'cancel'}
        )[:1]

        if not move:
            move = self.env['stock.move'].create({
                'name': self.product_id.display_name,
                'product_id': self.product_id.id,
                'product_uom_qty': self.quantity,
                'product_uom': self.product_id.uom_id.id,
                'picking_id': picking.id,
                'location_id': picking.location_id.id,
                'location_dest_id': picking.location_dest_id.id,
            })
        else:
            move = move[0]

        if move.state == 'draft':
            move._action_confirm()

        return move

    def _get_or_create_move_line(self, move):
        self.ensure_one()
        picking = self.picking_id
        candidate = move.move_line_ids.filtered(
            lambda l: l.product_id == self.product_id
            and l.location_id == picking.location_id
            and l.location_dest_id == picking.location_dest_id
            and not l.lot_id
        )[:1]
        if candidate:
            return candidate[0]

        return self.env['stock.move.line'].create({
            'picking_id': picking.id,
            'move_id': move.id,
            'product_id': self.product_id.id,
            'product_uom_id': move.product_uom.id,
            'location_id': picking.location_id.id,
            'location_dest_id': picking.location_dest_id.id,
            'quantity': 0.0,
        })

    def _apply_scanned_quantity(self):
        self.ensure_one()
        if not self.product_id:
            if self.create_product_if_missing:
                self._create_product_from_scan()
            else:
                raise UserError('Please scan a valid barcode!')
        if self.quantity <= 0:
            raise UserError('Quantity must be positive.')

        move = self._get_or_create_move()
        line = self._get_or_create_move_line(move)

        line.quantity += self.quantity

        if float_is_zero(move.product_uom_qty, precision_rounding=move.product_uom.rounding):
            move.product_uom_qty = line.quantity

        self.env['automotive.audit.log'].log_change(
            action='custom',
            record=self.picking_id,
            description='Barcode scan',
            new_values={
                'barcode': self.barcode,
                'product_id': self.product_id.id,
                'qty': self.quantity,
                'picking_id': self.picking_id.id,
                'move_id': move.id,
                'move_line_id': line.id,
            },
        )

        return move, line

    def action_add_to_reception(self):
        """Add scanned product to reception"""
        self.ensure_one()
        self._apply_scanned_quantity()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Product Added',
                'message': f'Added {self.quantity} x {self.product_id.name}',
                'type': 'success',
                'next': {'type': 'ir.actions.act_window_close'},
            }
        }

    def action_add_and_scan_next(self):
        """Add scanned product then open a fresh wizard for the next scan."""
        self.ensure_one()
        self._apply_scanned_quantity()

        next_wizard = self.create({'picking_id': self.picking_id.id})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.barcode.scan.wizard',
            'res_id': next_wizard.id,
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_picking_id': self.picking_id.id},
        }
