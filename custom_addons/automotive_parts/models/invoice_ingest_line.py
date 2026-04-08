# -*- coding: utf-8 -*-
import os
from math import ceil

from odoo import api, fields, models
from odoo.exceptions import UserError

from .invoice_ingest_shared import snapshot_record


class InvoiceIngestJobLine(models.Model):
    _name = 'invoice.ingest.job.line'
    _description = 'Invoice Ingest Job Line'
    _order = 'sequence, id'
    _AUDIT_FIELDS = {
        'sequence',
        'quantity',
        'product_code',
        'product_code_raw',
        'manual_internal_code',
        'manual_barcode_value',
        'supplier_brand',
        'supplier_brand_id',
        'product_description',
        'unit_price',
        'discount_percent',
        'vat_rate',
        'markup_percent',
        'product_id',
        'match_method',
        'match_confidence',
    }

    job_id = fields.Many2one('invoice.ingest.job', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    currency_id = fields.Many2one(related='job_id.currency_id', store=True, readonly=True)
    quantity = fields.Float(default=1.0)
    product_code = fields.Char('Product Code')
    product_code_raw = fields.Char('Raw Product Code')
    # Brand/manufacturer parsed from the line text (not the invoice vendor partner).
    supplier_brand = fields.Char('Brand')
    supplier_brand_id = fields.Integer('Brand Supplier ID')
    product_description = fields.Char('Description')
    unit_price = fields.Monetary(string='PU fara TVA', currency_field='currency_id')
    discount_percent = fields.Float(string='Reducere %', default=0.0)
    discounted_unit_price = fields.Monetary(
        string='Pret unit. disc.',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    unit_price_incl_vat = fields.Monetary(
        string='PU cu TVA',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    vat_rate = fields.Float(string='TVA %')
    vat_unit_amount = fields.Monetary(
        string='TVA pe unit.',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    subtotal = fields.Monetary(
        string='Total fara TVA',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    subtotal_incl_vat = fields.Monetary(
        string='Total cu TVA',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    markup_percent = fields.Float(string='Adaos %', default=lambda self: self._default_markup_percent())
    markup_amount = fields.Monetary(
        string='Adaos',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    sale_price_excl_vat = fields.Monetary(
        string='Pret vanzare fara TVA',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    sale_price_incl_vat = fields.Monetary(
        string='Pret unit. cu TVA',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    product_id = fields.Many2one('product.product', string='Matched Product')
    matched_ean = fields.Char(related='product_id.barcode', string='EAN', readonly=True)
    manual_internal_code = fields.Char('Manual Cod Intern')
    manual_barcode_value = fields.Char('Manual Cod de bare')
    matched_internal_code = fields.Char(
        string='Cod Intern',
        compute='_compute_label_display_fields',
        inverse='_inverse_label_display_fields',
        store=True,
    )
    label_display_name = fields.Char(
        string='Denumire',
        compute='_compute_label_display_fields',
        store=True,
    )
    label_barcode_value = fields.Char(
        string='Cod de bare',
        compute='_compute_label_display_fields',
        inverse='_inverse_label_display_fields',
        store=True,
    )
    match_method = fields.Char('Match Method')
    match_confidence = fields.Float('Match Confidence (%)')
    match_status = fields.Selection(
        [
            ('matched', 'Matched'),
            ('not_found', 'Not Found'),
            ('manual', 'Manual'),
        ],
        compute='_compute_match_status',
        store=True,
    )

    def _audit_snapshot(self, field_names=None):
        return snapshot_record(self, field_names or self._AUDIT_FIELDS)

    def write(self, vals):
        context = dict(self.env.context or {})
        tracked_fields = [field_name for field_name in vals.keys() if field_name in self._AUDIT_FIELDS]
        old_by_id = {}
        if tracked_fields and context.get('skip_audit_log') is not True:
            old_by_id = {line.id: line._audit_snapshot(tracked_fields) for line in self}

        result = super().write(vals)

        if tracked_fields and context.get('skip_audit_log') is not True:
            for line in self.filtered('job_id'):
                old_values = dict(old_by_id.get(line.id) or {})
                new_values = line._audit_snapshot(tracked_fields)
                line.job_id._audit_log(
                    action='custom',
                    description=(
                        f'Invoice ingest line updated: {line.job_id.display_name} / '
                        f'line {line.sequence}'
                    ),
                    old_values={
                        'line_id': line.id,
                        'sequence': old_values.get('sequence', line.sequence),
                        **old_values,
                    },
                    new_values={
                        'line_id': line.id,
                        'sequence': line.sequence,
                        **new_values,
                    },
                )

        return result

    @api.model
    def _default_markup_percent(self):
        raw = (
            os.getenv('INVOICE_INGEST_DEFAULT_MARKUP_PERCENT')
            or self.env['ir.config_parameter'].sudo().get_param('automotive.invoice_ingest_default_markup_percent')
            or '25'
        )
        try:
            return float(raw)
        except Exception:
            return 25.0

    @api.depends('quantity', 'unit_price', 'discount_percent', 'vat_rate', 'markup_percent')
    def _compute_financials(self):
        for line in self:
            quantity = line.quantity or 0.0
            unit_price = line.unit_price or 0.0
            discount_pct = line.discount_percent or 0.0
            vat_pct = line.vat_rate or 0.0
            markup_pct = line.markup_percent or 0.0

            discounted_unit = unit_price * (1 - (discount_pct / 100.0))
            vat_unit = discounted_unit * (vat_pct / 100.0)
            subtotal_excl = quantity * discounted_unit
            subtotal_incl = subtotal_excl + (quantity * vat_unit)
            markup_value = discounted_unit * (markup_pct / 100.0)
            sale_excl = discounted_unit + markup_value
            sale_incl = sale_excl * (1 + (vat_pct / 100.0))

            line.discounted_unit_price = discounted_unit
            line.unit_price_incl_vat = discounted_unit + vat_unit
            line.vat_unit_amount = vat_unit
            line.subtotal = subtotal_excl
            line.subtotal_incl_vat = subtotal_incl
            line.markup_amount = markup_value
            line.sale_price_excl_vat = sale_excl
            line.sale_price_incl_vat = sale_incl

    @api.depends('product_id', 'product_code', 'match_method', 'match_confidence')
    def _compute_match_status(self):
        for line in self:
            if line.product_id:
                method = (line.match_method or '').lower()
                confidence = line.match_confidence or 0.0
                if method.startswith('exact:') or (method.startswith('lookup') and confidence >= 90.0):
                    line.match_status = 'matched'
                else:
                    line.match_status = 'manual'
            else:
                line.match_status = 'not_found'

    @api.depends(
        'product_id',
        'product_id.display_name',
        'product_id.barcode',
        'product_id.barcode_internal',
        'product_id.default_code',
        'product_description',
        'product_code',
        'product_code_raw',
        'manual_internal_code',
        'manual_barcode_value',
    )
    def _compute_label_display_fields(self):
        for line in self:
            line.label_display_name = line.product_id.display_name or line.product_description or ''
            line.matched_internal_code = line.manual_internal_code or line._default_internal_code_value()
            line.label_barcode_value = line.manual_barcode_value or line._default_barcode_value()

    def _default_internal_code_value(self):
        self.ensure_one()
        return self.product_id.default_code or ''

    def _default_barcode_value(self):
        self.ensure_one()
        return (
            self.product_id.barcode
            or self.product_id.barcode_internal
            or self.product_code
            or self.product_code_raw
            or self.product_id.default_code
            or ''
        )

    def _inverse_label_display_fields(self):
        for line in self:
            default_internal_code = line._default_internal_code_value()
            default_barcode_value = line._default_barcode_value()
            line.manual_internal_code = (
                False
                if (line.matched_internal_code or '') == default_internal_code
                else (line.matched_internal_code or False)
            )
            line.manual_barcode_value = (
                False
                if (line.label_barcode_value or '') == default_barcode_value
                else (line.label_barcode_value or False)
            )

    def _prepare_match_write_values(self, resolved):
        self.ensure_one()
        return {
            'product_code_raw': self.product_code_raw or resolved.get('product_code_raw') or self.product_code,
            'product_code': resolved.get('product_code') or False,
            'supplier_brand': resolved.get('supplier_brand') or '',
            'supplier_brand_id': resolved.get('supplier_brand_id') or False,
            'product_id': resolved.get('matched_product_id') or False,
            'match_method': resolved.get('match_method') or False,
            'match_confidence': resolved.get('match_confidence', 0.0),
        }

    def _apply_resolved_match(self, resolved, write=False):
        self.ensure_one()
        values = self._prepare_match_write_values(resolved)
        if write:
            self.with_context(skip_audit_log=True).write(values)
            return values

        for field_name, value in values.items():
            self[field_name] = value
        return values

    @api.onchange('product_code')
    def _onchange_product_code(self):
        for line in self:
            if line.product_id or not line.product_code or not line.job_id:
                continue
            resolved = line.job_id._resolve_line_match_data(
                raw_code=line.product_code_raw or line.product_code,
                product_code=line.product_code,
                product_description=line.product_description,
                supplier=line.job_id.partner_id,
                supplier_brand=line.supplier_brand,
            )
            line._apply_resolved_match(resolved, write=False)

    @api.onchange('job_id')
    def _onchange_job_id_defaults(self):
        for line in self:
            if line.job_id and not line.vat_rate:
                line.vat_rate = line.job_id.vat_rate or 0.0

    @api.onchange('product_id')
    def _onchange_product_id_brand(self):
        for line in self:
            if not line.job_id or not line.product_id:
                continue
            canonical_brand, canonical_supplier_id = line.job_id._brand_from_matched_product(line.product_id)
            if canonical_brand:
                line.supplier_brand = canonical_brand
            line.supplier_brand_id = canonical_supplier_id or False

    def action_try_match(self):
        for line in self:
            if not line.job_id:
                continue
            resolved = line.job_id._resolve_line_match_data(
                raw_code=line.product_code_raw or line.product_code,
                product_code=line.product_code,
                product_description=line.product_description,
                supplier=line.job_id.partner_id,
                supplier_brand=line.supplier_brand,
            )
            line._apply_resolved_match(resolved, write=True)
            line.job_id._audit_log(
                action='custom',
                description=f'Invoice ingest line match attempted: {line.job_id.display_name} / line {line.sequence}',
                new_values={
                    'line_id': line.id,
                    'sequence': line.sequence,
                    'product_code': line.product_code,
                    'supplier_brand': line.supplier_brand,
                    'product_id': line.product_id.id if line.product_id else False,
                    'match_method': line.match_method,
                    'match_confidence': line.match_confidence,
                    'match_status': line.match_status,
                },
            )
        return True

    def action_open_tecdoc_match(self):
        self.ensure_one()
        search_value = (
            (self.product_code or '').strip()
            or (self.product_code_raw or '').strip()
            or (self.label_barcode_value or '').strip()
            or ''
        )
        lookup_type = 'article_no'
        if not ((self.product_code or '').strip() or (self.product_code_raw or '').strip()):
            barcode_value = (self.label_barcode_value or '').strip()
            if barcode_value:
                lookup_type = 'ean'
                search_value = barcode_value
        if not search_value:
            raise UserError('No product code or barcode is available for TecDoc search on this line.')

        wizard = self.env['tecdoc.sync.wizard'].create({
            'lookup_type': lookup_type,
            'article_number': search_value or '',
            'supplier_id': self.supplier_brand_id or 0,
            'invoice_ingest_line_id': self.id,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': 'Match TecDoc',
            'res_model': 'tecdoc.sync.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_clear_match(self):
        snapshots = {
            line.id: {
                'job_id': line.job_id.id if line.job_id else False,
                'sequence': line.sequence,
                'product_id': line.product_id.id if line.product_id else False,
                'supplier_brand_id': line.supplier_brand_id,
                'match_method': line.match_method,
                'match_confidence': line.match_confidence,
            }
            for line in self
        }
        self.with_context(skip_audit_log=True).write({
            'product_id': False,
            'supplier_brand_id': False,
            'match_method': False,
            'match_confidence': 0.0,
        })
        for line in self.filtered('job_id'):
            line.job_id._audit_log(
                action='custom',
                description=f'Invoice ingest line match cleared: {line.job_id.display_name} / line {line.sequence}',
                old_values=snapshots.get(line.id),
                new_values={
                    'line_id': line.id,
                    'sequence': line.sequence,
                    'product_id': False,
                    'supplier_brand_id': False,
                    'match_method': False,
                    'match_confidence': 0.0,
                    'match_status': line.match_status,
                },
            )
        return True

    def _build_label_payload(self, qty):
        self.ensure_one()
        quantity = max(int(qty or 1), 1)
        product = self.product_id
        if product:
            return product._prepare_label_payload(
                name=self.product_description or product.display_name,
                barcode=self.label_barcode_value,
                product_code=self.product_code or self.product_code_raw or product.supplier_code or product.default_code,
                internal_code=self.matched_internal_code or product.default_code,
                price=self.sale_price_incl_vat,
                brand=self.supplier_brand or product.tecdoc_supplier_name or product.main_supplier_id.name,
                qty=quantity,
            )
        return self.env['product.product']._prepare_label_payload_from_values(
            name=self.product_description,
            barcode=self.label_barcode_value,
            product_code=self.product_code or self.product_code_raw,
            internal_code='',
            price=self.sale_price_incl_vat,
            brand=self.supplier_brand,
            qty=quantity,
        )

    def action_generate_label(self):
        self.ensure_one()
        return self.env['automotive.label.print.wizard'].open_wizard(
            labels=[self._build_label_payload(qty=1)],
            source_record=self,
            label_count=max(int(ceil(self.quantity or 1.0)), 1),
            job_name=self.job_id.display_name or self.display_name,
        )

    def _prepare_label_payload(self):
        self.ensure_one()
        return self._build_label_payload(qty=max(int(ceil(self.quantity or 1.0)), 1))
