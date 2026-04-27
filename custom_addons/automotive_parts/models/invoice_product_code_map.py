# -*- coding: utf-8 -*-
from odoo import api, fields, models

from .invoice_ingest_code_utils import compact_code, normalize_code_value, prefix_stripped_code_variants


class InvoiceProductCodeMap(models.Model):
    _name = 'invoice.product.code.map'
    _description = 'Invoice Product Code Mapping'
    _order = 'last_used_at desc, confirmed_at desc, id desc'

    name = fields.Char(compute='_compute_name', store=True)
    active = fields.Boolean(default=True, index=True)
    company_id = fields.Many2one(
        'res.company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    invoice_supplier_id = fields.Many2one(
        'res.partner',
        string='Invoice Supplier',
        required=True,
        index=True,
        ondelete='cascade',
    )
    raw_code = fields.Char(required=True)
    raw_code_key = fields.Char(required=True, index=True)
    normalized_code = fields.Char()
    normalized_code_key = fields.Char(index=True)
    prefix = fields.Char(index=True)
    prefix_key = fields.Char(index=True)
    supplier_brand = fields.Char()
    supplier_brand_key = fields.Char(default='', index=True)
    tecdoc_supplier_id = fields.Integer(index=True)
    tecdoc_article_id = fields.Integer(index=True)
    product_id = fields.Many2one(
        'product.product',
        required=True,
        index=True,
        ondelete='restrict',
    )
    confirmation_source = fields.Selection(
        [
            ('manual', 'Manual'),
            ('tecdoc_candidate', 'TecDoc Candidate'),
            ('tecdoc_sync', 'TecDoc Sync'),
            ('imported', 'Imported'),
        ],
        default='manual',
        required=True,
        index=True,
    )
    confirmed_by_id = fields.Many2one(
        'res.users',
        default=lambda self: self.env.user,
        readonly=True,
    )
    confirmed_at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    hit_count = fields.Integer(default=0, readonly=True)
    last_used_at = fields.Datetime(readonly=True)

    _sql_constraints = [
        (
            'invoice_product_code_map_unique',
            'unique(company_id, invoice_supplier_id, raw_code_key, supplier_brand_key)',
            'This supplier/code/brand mapping already exists.',
        ),
    ]

    @api.depends('invoice_supplier_id', 'raw_code', 'normalized_code', 'product_id')
    def _compute_name(self):
        for rec in self:
            supplier = rec.invoice_supplier_id.display_name or ''
            target = rec.normalized_code or rec.product_id.default_code or rec.product_id.display_name or ''
            rec.name = f"{supplier}: {rec.raw_code} -> {target}".strip()

    @api.model
    def _keys_from_values(self, vals):
        out = dict(vals)
        raw_code = normalize_code_value(out.get('raw_code'))
        normalized_code = normalize_code_value(out.get('normalized_code'))
        supplier_brand = normalize_code_value(out.get('supplier_brand'))

        out['raw_code'] = raw_code
        out['raw_code_key'] = compact_code(raw_code)
        out['normalized_code'] = normalized_code or False
        out['normalized_code_key'] = compact_code(normalized_code) if normalized_code else False
        out['supplier_brand'] = supplier_brand or False
        out['supplier_brand_key'] = compact_code(supplier_brand) if supplier_brand else ''

        prefix = normalize_code_value(out.get('prefix'))
        if not prefix:
            stripped = prefix_stripped_code_variants(raw_code)
            if stripped:
                compact_raw = compact_code(raw_code)
                compact_stripped = stripped[0]
                prefix = compact_raw[: len(compact_raw) - len(compact_stripped)]
        out['prefix'] = prefix or False
        out['prefix_key'] = compact_code(prefix) if prefix else False
        return out

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._keys_from_values(vals) for vals in vals_list])

    def write(self, vals):
        key_fields = {'raw_code', 'normalized_code', 'supplier_brand', 'prefix'}
        if key_fields.intersection(vals):
            for rec in self:
                rec_vals = {
                    'raw_code': rec.raw_code,
                    'normalized_code': rec.normalized_code,
                    'supplier_brand': rec.supplier_brand,
                    'prefix': rec.prefix,
                    **vals,
                }
                super(InvoiceProductCodeMap, rec).write(rec._keys_from_values(rec_vals))
            return True
        return super().write(vals)

    @api.model
    def _storage_ready(self):
        """Return False when deployed code was loaded before module upgrade created the table."""
        self.env.cr.execute('SELECT to_regclass(%s)', (self._table,))
        row = self.env.cr.fetchone()
        return bool(row and row[0])

    @api.model
    def _find_for_line(self, invoice_supplier, raw_code, supplier_brand=''):
        if not self._storage_ready():
            return self.browse()

        Mapping = self.sudo()
        supplier = invoice_supplier[:1] if invoice_supplier else self.env['res.partner']
        raw_key = compact_code(raw_code)
        if not supplier or not raw_key:
            return self.browse()

        brand_key = compact_code(supplier_brand)
        domain = [
            ('active', '=', True),
            ('company_id', '=', self.env.company.id),
            ('invoice_supplier_id', '=', supplier.id),
            ('raw_code_key', '=', raw_key),
        ]
        if brand_key:
            mapping = Mapping.search(domain + [('supplier_brand_key', '=', brand_key)], limit=1)
            if mapping:
                return mapping
            return Mapping.search(domain + [('supplier_brand_key', '=', '')], limit=1)

        mappings = Mapping.search(domain, limit=2)
        if len(mappings) == 1:
            return mappings
        return self.browse()

    def _record_usage(self):
        now = fields.Datetime.now()
        for rec in self:
            rec.sudo().write({
                'hit_count': (rec.hit_count or 0) + 1,
                'last_used_at': now,
            })

    @api.model
    def create_or_update_from_line(
        self,
        line,
        product,
        normalized_code='',
        confirmation_source='manual',
        tecdoc_supplier_id=False,
        tecdoc_article_id=False,
    ):
        if not self._storage_ready():
            return self.browse()

        line = line.exists()[:1]
        product = product.exists()[:1]
        if not line or not product or not line.job_id.partner_id:
            return self.browse()

        raw_code = line.product_code_raw or line.product_code
        if not raw_code:
            return self.browse()

        product_tecdoc_id = product.tecdoc_id or ''
        resolved_article_id = tecdoc_article_id or (int(product_tecdoc_id) if str(product_tecdoc_id).isdigit() else 0)
        vals = {
            'company_id': line.job_id.company_id.id if 'company_id' in line.job_id._fields and line.job_id.company_id else self.env.company.id,
            'invoice_supplier_id': line.job_id.partner_id.id,
            'raw_code': raw_code,
            'normalized_code': normalized_code or product.tecdoc_article_no or product.default_code or line.product_code,
            'supplier_brand': line.supplier_brand or product.tecdoc_supplier_name or '',
            'tecdoc_supplier_id': tecdoc_supplier_id or product.tecdoc_supplier_id or 0,
            'tecdoc_article_id': resolved_article_id,
            'product_id': product.id,
            'confirmation_source': confirmation_source,
            'confirmed_by_id': self.env.user.id,
        }
        vals = self._keys_from_values(vals)
        Mapping = self.sudo()
        mapping = Mapping.search([
            ('company_id', '=', vals['company_id']),
            ('invoice_supplier_id', '=', vals['invoice_supplier_id']),
            ('raw_code_key', '=', vals['raw_code_key']),
            ('supplier_brand_key', '=', vals['supplier_brand_key']),
        ], limit=1)
        if mapping:
            mapping.write({
                'normalized_code': vals.get('normalized_code') or False,
                'normalized_code_key': vals.get('normalized_code_key') or False,
                'prefix': vals.get('prefix') or False,
                'prefix_key': vals.get('prefix_key') or False,
                'tecdoc_supplier_id': vals.get('tecdoc_supplier_id') or 0,
                'tecdoc_article_id': vals.get('tecdoc_article_id') or 0,
                'product_id': product.id,
                'confirmation_source': confirmation_source,
                'active': True,
            })
            return mapping
        return Mapping.create(vals)
