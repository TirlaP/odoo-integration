# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError

from .tecdoc_fast_models import _normalize_key, _product_template_action


class TecDocLookupWizard(models.TransientModel):
    _name = 'tecdoc.lookup.wizard'
    _description = 'TecDoc Lookup'

    search_value = fields.Char(required=True)
    lookup_type = fields.Selection(
        [
            ('auto', 'Auto'),
            ('article', 'Article Number'),
            ('oem', 'OEM Number'),
            ('ean', 'EAN'),
            ('equivalent', 'Equivalent'),
        ],
        default='auto',
        required=True,
    )
    result_ids = fields.One2many('tecdoc.lookup.result', 'wizard_id', string='Results')
    result_count = fields.Integer(compute='_compute_result_count')
    live_error = fields.Text(readonly=True)

    @api.depends('result_ids')
    def _compute_result_count(self):
        for rec in self:
            rec.result_count = len(rec.result_ids)

    def _wizard_action(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Căutare TecDoc',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    @api.model
    def open_lookup(self, search_value, lookup_type='auto'):
        wizard = self.create({
            'search_value': search_value,
            'lookup_type': lookup_type or 'auto',
        })
        return wizard.action_search()

    def action_search(self):
        self.ensure_one()
        value = (self.search_value or '').strip()
        if not value:
            raise UserError('Enter a search value.')

        self.result_ids.unlink()
        self.live_error = False

        rows = []
        seen_products = set()
        seen_variants = set()
        local_variant_count = 0

        def add_product(product, reason, source='local_product'):
            product = product.exists()[:1]
            if not product:
                return
            template = product.product_tmpl_id
            if template.id in seen_products:
                return
            seen_products.add(template.id)
            rows.append((0, 0, {
                'group_type': 'product',
                'match_source': source,
                'match_reason': reason,
                'product_tmpl_id': template.id,
                'product_id': product.id,
                'article_no': template.tecdoc_article_no or product.default_code or product.barcode_internal or product.barcode,
                'article_name': template.name,
                'supplier_id': template.tecdoc_supplier_id or False,
                'supplier_name': template.tecdoc_supplier_name or False,
                'image_url': template.tecdoc_image_url or False,
                'sequence': 10,
            }))

        def add_variant(variant, reason, source='local_catalog'):
            nonlocal local_variant_count
            variant = variant.exists()[:1]
            if not variant or variant.id in seen_variants:
                return
            seen_variants.add(variant.id)
            if source == 'local_catalog':
                local_variant_count += 1
            template = variant.product_tmpl_id
            product = template.product_variant_id if template else self.env['product.product']
            group_type = 'product' if template else ('live' if source == 'live_tecdoc' else 'reference')
            sequence = 20 if group_type == 'product' else (30 if group_type == 'reference' else 40)
            if template:
                if template.id in seen_products:
                    return
                seen_products.add(template.id)
            rows.append((0, 0, {
                'group_type': group_type,
                'match_source': source,
                'match_reason': reason,
                'variant_id': variant.id,
                'product_tmpl_id': template.id if template else False,
                'product_id': product.id if product else False,
                'article_id': variant.article_id,
                'article_no': variant.article_no,
                'article_name': variant.article_product_name,
                'supplier_id': variant.supplier_external_id or False,
                'supplier_name': variant.supplier_name or (variant.supplier_id.name if variant.supplier_id else False),
                'image_url': variant.image_url or False,
                'sequence': sequence,
            }))

        for product, reason in self._local_product_matches(value):
            add_product(product, reason)

        for variant, reason in self._local_variant_matches(value):
            add_variant(variant, reason)

        if not local_variant_count:
            for variant, reason in self._live_variant_matches(value):
                add_variant(variant, reason, source='live_tecdoc')

        self.write({'result_ids': rows})
        return self._wizard_action()

    def _local_product_matches(self, value):
        Product = self.env['product.product']
        fields_to_check = (
            'default_code',
            'barcode',
            'barcode_internal',
            'supplier_code',
            'tecdoc_article_no',
            'tecdoc_ean',
        )
        seen = set()
        for field_name in fields_to_check:
            if field_name not in Product._fields:
                continue
            for product in Product.search([(field_name, '=', value)], limit=20):
                if product.id in seen:
                    continue
                seen.add(product.id)
                yield product, f'exact:{field_name}'

    def _local_variant_matches(self, value):
        key = _normalize_key(value)
        if not key:
            return

        Variant = self.env['tecdoc.article.variant']
        Ean = self.env['tecdoc.article.variant.ean']
        Oem = self.env['tecdoc.oem.number']
        Cross = self.env['tecdoc.cross.number']
        CrossLink = self.env['tecdoc.article.variant.cross']
        Relation = self.env['tecdoc.article.relation']
        mode = self.lookup_type
        seen = set()

        def emit(variants, reason):
            for variant in variants:
                if variant.id in seen:
                    continue
                seen.add(variant.id)
                yield variant, reason

        if mode in ('auto', 'article', 'equivalent'):
            yield from emit(Variant.search([('article_no_key', '=', key)], limit=80), 'local:article')

        if mode in ('auto', 'ean'):
            yield from emit(Ean.search([('ean_key', '=', key)], limit=80).mapped('variant_id'), 'local:ean')

        if mode in ('auto', 'oem'):
            oem_numbers = Oem.search([('number_key', '=', key)], limit=80)
            yield from emit(Variant.search([('oem_number_ids', 'in', oem_numbers.ids)], limit=120), 'local:oem')

        if mode in ('auto', 'equivalent'):
            cross_numbers = Cross.search([('number_key', '=', key)], limit=80)
            cross_links = CrossLink.search([('cross_number_id', 'in', cross_numbers.ids)], limit=120)
            yield from emit(cross_links.mapped('variant_id'), 'local:cross')

            direct = Variant.search([('article_no_key', '=', key)], limit=80)
            relations = Relation.search([
                '|',
                ('source_variant_id', 'in', direct.ids),
                ('target_variant_id', 'in', direct.ids),
            ], limit=160)
            yield from emit((relations.mapped('source_variant_id') | relations.mapped('target_variant_id')), 'local:relation')

    def _live_variant_matches(self, value):
        api = self.env['tecdoc.api']._get_default_api()
        if not api:
            return []

        attempts = []
        if self.lookup_type == 'article':
            attempts = [('live:article', lambda: api.search_articles_by_article_no(value, article_type='ArticleNumber'))]
        elif self.lookup_type == 'oem':
            attempts = [('live:oem', lambda: api.search_articles_by_oem_no(value))]
        elif self.lookup_type == 'ean':
            attempts = [('live:ean', lambda: api.search_articles_by_ean(value))]
        elif self.lookup_type == 'equivalent':
            attempts = [('live:equivalent', lambda: api.search_articles_by_equivalent_no(value))]
        else:
            attempts = [
                ('live:article', lambda: api.search_articles_by_article_no(value, article_type='ArticleNumber')),
                ('live:oem', lambda: api.search_articles_by_oem_no(value)),
                ('live:ean', lambda: api.search_articles_by_ean(value)),
                ('live:equivalent', lambda: api.search_articles_by_equivalent_no(value)),
            ]

        Variant = self.env['tecdoc.article.variant'].sudo()
        last_error = None
        for reason, fetch in attempts:
            try:
                articles = api._extract_articles(fetch())
            except UserError as err:
                last_error = err
                articles = []
            variants = []
            for article in articles[:80]:
                variant = Variant._upsert_light_reference(article)
                if variant:
                    variants.append((variant, reason))
            if variants:
                return variants

        if last_error:
            self.live_error = str(last_error)
        return []


class TecDocLookupResult(models.TransientModel):
    _name = 'tecdoc.lookup.result'
    _description = 'TecDoc Lookup Result'
    _order = 'sequence, group_type, supplier_name, article_no'

    wizard_id = fields.Many2one('tecdoc.lookup.wizard', required=True, ondelete='cascade')
    sequence = fields.Integer(default=50)
    group_type = fields.Selection(
        [
            ('product', 'Produse existente'),
            ('reference', 'Referințe TecDoc'),
            ('live', 'Rezultate live TecDoc'),
        ],
        default='reference',
        index=True,
    )
    match_source = fields.Char(readonly=True)
    match_reason = fields.Char(readonly=True)
    variant_id = fields.Many2one('tecdoc.article.variant', readonly=True)
    product_tmpl_id = fields.Many2one('product.template', readonly=True)
    product_id = fields.Many2one('product.product', readonly=True)
    article_id = fields.Integer(readonly=True)
    supplier_id = fields.Integer(readonly=True)
    supplier_name = fields.Char(readonly=True)
    article_no = fields.Char(readonly=True)
    article_name = fields.Char(readonly=True)
    image_url = fields.Char(readonly=True)
    link_product_tmpl_id = fields.Many2one('product.template', string='Leagă produs')

    def action_open_product(self):
        self.ensure_one()
        if not self.product_tmpl_id:
            raise UserError('No Odoo product linked to this result.')
        return _product_template_action(self.product_tmpl_id)

    def action_open_variant(self):
        self.ensure_one()
        if not self.variant_id:
            raise UserError('No TecDoc reference linked to this result.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'TecDoc Variant',
            'res_model': 'tecdoc.article.variant',
            'res_id': self.variant_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_create_product(self):
        self.ensure_one()
        if not self.variant_id:
            raise UserError('No TecDoc reference linked to this result.')
        return self.variant_id.action_create_product_from_reference()

    def action_link_existing(self):
        self.ensure_one()
        if not self.variant_id:
            raise UserError('No TecDoc reference linked to this result.')
        if not self.link_product_tmpl_id:
            raise UserError('Select an existing product first.')
        template = self.link_product_tmpl_id
        vals = {
            'product_tmpl_id': template.id,
            'is_reference_only': False,
        }
        self.variant_id.write(vals)
        update_vals = {}
        if self.variant_id.article_id and self.variant_id.article_id > 0 and not template.tecdoc_id:
            update_vals['tecdoc_id'] = str(self.variant_id.article_id)
        if self.variant_id.article_no and not template.tecdoc_article_no:
            update_vals['tecdoc_article_no'] = self.variant_id.article_no
        if self.variant_id.supplier_external_id and not template.tecdoc_supplier_id:
            update_vals['tecdoc_supplier_id'] = self.variant_id.supplier_external_id
        if self.variant_id.supplier_name and not template.tecdoc_supplier_name:
            update_vals['tecdoc_supplier_name'] = self.variant_id.supplier_name
        if self.variant_id.image_url and not template.tecdoc_image_url:
            update_vals['tecdoc_image_url'] = self.variant_id.image_url
        if update_vals:
            template.write(update_vals)
        self.write({
            'product_tmpl_id': template.id,
            'product_id': template.product_variant_id.id,
            'group_type': 'product',
        })
        return _product_template_action(template)

    def action_enrich_reference(self):
        self.ensure_one()
        if not self.variant_id:
            raise UserError('No TecDoc reference linked to this result.')
        return self.variant_id.action_enrich_reference()

    def action_search_article(self):
        self.ensure_one()
        return self.env['tecdoc.lookup.wizard'].open_lookup(self.article_no, lookup_type='article')
