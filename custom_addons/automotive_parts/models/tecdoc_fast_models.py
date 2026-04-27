# -*- coding: utf-8 -*-
import re
import zlib

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


def _normalize_key(value):
    value = (value or '').strip().upper()
    return re.sub(r'[^0-9A-Z]+', '', value)


def _fallback_article_id(supplier_id, article_no):
    key = f"{supplier_id or 0}:{_normalize_key(article_no)}"
    checksum = zlib.crc32(key.encode('utf-8')) % 2147483647
    return -(checksum or 1)


def _article_identity_key(article_id, supplier_id, article_no):
    if article_id and int(article_id) > 0:
        return f"id:{int(article_id)}"
    return f"supplier:{supplier_id or 0}:article:{_normalize_key(article_no)}"


def _product_template_action(template):
    template.ensure_one()
    return {
        'type': 'ir.actions.act_window',
        'name': 'Product',
        'res_model': 'product.template',
        'res_id': template.id,
        'view_mode': 'form',
        'target': 'current',
    }


class TecDocSupplier(models.Model):
    _name = 'tecdoc.supplier'
    _description = 'TecDoc Supplier'
    _order = 'name, supplier_id'

    supplier_id = fields.Integer(index=True, required=True)
    name = fields.Char(required=True, index=True)
    supplier_match_code = fields.Char(index=True)
    supplier_logo_name = fields.Char()
    active = fields.Boolean(default=True, index=True)

    _sql_constraints = [
        ('tecdoc_supplier_id_unique', 'unique(supplier_id)', 'TecDoc Supplier ID must be unique.'),
    ]


class TecDocVehicle(models.Model):
    _name = 'tecdoc.vehicle'
    _description = 'TecDoc Vehicle'
    _order = 'manufacturer_name, model_name, type_engine_name, vehicle_id'

    vehicle_id = fields.Integer(index=True, required=True)
    model_id = fields.Integer(index=True)

    manufacturer_name = fields.Char(index=True)
    model_name = fields.Char(index=True)
    type_engine_name = fields.Char(index=True)

    construction_interval_start = fields.Date(index=True)
    construction_interval_end = fields.Date(index=True)

    variant_ids = fields.Many2many(
        'tecdoc.article.variant',
        'tecdoc_article_variant_vehicle_rel',
        'vehicle_id',
        'variant_id',
        string='Variants',
    )

    _sql_constraints = [
        ('tecdoc_vehicle_id_unique', 'unique(vehicle_id)', 'TecDoc Vehicle ID must be unique.'),
    ]


class TecDocOemNumber(models.Model):
    _name = 'tecdoc.oem.number'
    _description = 'TecDoc OEM Number'
    _order = 'brand, display_no'

    brand = fields.Char(required=True, index=True)
    display_no = fields.Char(required=True, index=True)
    number_key = fields.Char(required=True, index=True)

    variant_ids = fields.Many2many(
        'tecdoc.article.variant',
        'tecdoc_article_variant_oem_rel',
        'oem_id',
        'variant_id',
        string='Variants',
    )

    _sql_constraints = [
        ('tecdoc_oem_unique', 'unique(brand, number_key)', 'OEM number must be unique per brand.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('number_key') and vals.get('display_no'):
                vals['number_key'] = _normalize_key(vals.get('display_no'))
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('display_no') and not vals.get('number_key'):
            vals['number_key'] = _normalize_key(vals.get('display_no'))
        return super().write(vals)

    def action_lookup_oem_number(self):
        self.ensure_one()
        wizard = self.env['tecdoc.lookup.wizard'].create({
            'search_value': self.display_no,
            'lookup_type': 'oem',
        })
        return wizard.action_search()


class TecDocCrossNumber(models.Model):
    _name = 'tecdoc.cross.number'
    _description = 'TecDoc Cross Number'
    _order = 'manufacturer, display_no'

    manufacturer = fields.Char(required=True, index=True)
    display_no = fields.Char(required=True, index=True)
    number_key = fields.Char(required=True, index=True)

    cross_link_ids = fields.One2many('tecdoc.article.variant.cross', 'cross_number_id', string='Variant Links')

    _sql_constraints = [
        ('tecdoc_cross_unique', 'unique(manufacturer, number_key)', 'Cross number must be unique per manufacturer.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('number_key') and vals.get('display_no'):
                vals['number_key'] = _normalize_key(vals.get('display_no'))
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('display_no') and not vals.get('number_key'):
            vals['number_key'] = _normalize_key(vals.get('display_no'))
        return super().write(vals)

    def action_lookup_cross_number(self):
        self.ensure_one()
        wizard = self.env['tecdoc.lookup.wizard'].create({
            'search_value': self.display_no,
            'lookup_type': 'equivalent',
        })
        return wizard.action_search()


class TecDocCriteria(models.Model):
    _name = 'tecdoc.criteria'
    _description = 'TecDoc Criteria'
    _order = 'name'

    name = fields.Char(required=True, index=True)
    name_key = fields.Char(required=True, index=True)

    _sql_constraints = [
        ('tecdoc_criteria_name_unique', 'unique(name_key)', 'Criteria name must be unique.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name_key') and vals.get('name'):
                vals['name_key'] = _normalize_key(vals.get('name'))
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('name') and not vals.get('name_key'):
            vals['name_key'] = _normalize_key(vals.get('name'))
        return super().write(vals)


class TecDocArticleVariant(models.Model):
    _name = 'tecdoc.article.variant'
    _description = 'TecDoc Article Variant'
    _order = 'article_no, supplier_id'

    name = fields.Char(compute='_compute_name', store=True, index=True)

    article_id = fields.Integer(index=True, required=True)
    article_no = fields.Char(required=True, index=True)
    article_no_key = fields.Char(required=True, index=True)

    supplier_id = fields.Many2one('tecdoc.supplier', index=True, ondelete='restrict')
    supplier_name = fields.Char(index=True)
    supplier_external_id = fields.Integer(index=True, help='TecDoc supplierId (numeric)')
    identity_key = fields.Char(index=True)
    is_reference_only = fields.Boolean(default=True, index=True)
    last_enriched_at = fields.Datetime(index=True)

    product_tmpl_id = fields.Many2one('product.template', index=True, ondelete='set null')

    article_product_name = fields.Char(index=True)
    image_url = fields.Char()
    media_filename = fields.Char()
    media_type = fields.Char()

    # Relations (big lists; shown via smart buttons / separate views)
    vehicle_ids = fields.Many2many(
        'tecdoc.vehicle',
        'tecdoc_article_variant_vehicle_rel',
        'variant_id',
        'vehicle_id',
        string='Compatible Vehicles',
    )
    oem_number_ids = fields.Many2many(
        'tecdoc.oem.number',
        'tecdoc_article_variant_oem_rel',
        'variant_id',
        'oem_id',
        string='OEM Numbers',
    )

    # Stored counters for fast UI
    vehicle_count = fields.Integer(default=0)
    oem_count = fields.Integer(default=0)
    criteria_count = fields.Integer(default=0)
    cross_count = fields.Integer(default=0)
    ean_count = fields.Integer(default=0)

    _sql_constraints = [
        ('tecdoc_article_id_unique', 'unique(article_id)', 'TecDoc Article ID must be unique.'),
    ]

    @api.depends('article_no', 'supplier_id.name', 'supplier_name')
    def _compute_name(self):
        for rec in self:
            supplier = rec.supplier_id.name or rec.supplier_name or ''
            supplier = supplier.strip()
            if supplier:
                rec.name = f"{rec.article_no} [{supplier}]"
            else:
                rec.name = rec.article_no

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('article_no_key') and vals.get('article_no'):
                vals['article_no_key'] = _normalize_key(vals.get('article_no'))
            if not vals.get('identity_key') and vals.get('article_no'):
                vals['identity_key'] = _article_identity_key(
                    vals.get('article_id'),
                    vals.get('supplier_external_id'),
                    vals.get('article_no'),
                )
            if vals.get('product_tmpl_id'):
                vals['is_reference_only'] = False
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('article_no') and not vals.get('article_no_key'):
            vals['article_no_key'] = _normalize_key(vals.get('article_no'))
        if vals.get('product_tmpl_id'):
            vals = dict(vals)
            vals['is_reference_only'] = False
        return super().write(vals)

    @api.model
    def _article_values_from_payload(self, article):
        article = article if isinstance(article, dict) else {}
        article_no = (article.get('articleNo') or article.get('article_no') or '').strip()
        supplier_id_int = int(article.get('supplierId') or article.get('supplier_id') or 0)
        article_id = int(article.get('articleId') or article.get('article_id') or 0)
        if not article_id and article_no:
            article_id = _fallback_article_id(supplier_id_int, article_no)
        supplier_name = (
            article.get('supplierName')
            or article.get('supplier_name')
            or article.get('brandName')
            or article.get('brand_name')
            or ''
        ).strip()
        article_name = (
            article.get('articleProductName')
            or article.get('article_product_name')
            or article.get('articleName')
            or article.get('article_name')
            or article.get('genericArticleName')
            or article.get('generic_article_name')
            or ''
        ).strip()
        return {
            'article_id': article_id,
            'article_no': article_no,
            'article_no_key': _normalize_key(article_no),
            'supplier_external_id': supplier_id_int or False,
            'supplier_name': supplier_name or False,
            'article_product_name': article_name or False,
            'image_url': article.get('s3image') or article.get('imageUrl') or article.get('image_url') or False,
            'media_filename': article.get('articleMediaFileName') or article.get('mediaFileName') or False,
            'media_type': article.get('articleMediaType') or article.get('mediaType') or False,
            'identity_key': _article_identity_key(article_id, supplier_id_int, article_no),
        }

    @api.model
    def _upsert_light_reference(self, article):
        vals = self._article_values_from_payload(article)
        if not vals.get('article_no') or not vals.get('article_id'):
            return self.browse()

        Supplier = self.env['tecdoc.supplier'].sudo()
        supplier = self.env['tecdoc.supplier']
        if vals.get('supplier_external_id'):
            supplier = Supplier.search([('supplier_id', '=', vals['supplier_external_id'])], limit=1)
            if not supplier:
                supplier = Supplier.create({
                    'supplier_id': vals['supplier_external_id'],
                    'name': vals.get('supplier_name') or str(vals['supplier_external_id']),
                })
        elif vals.get('supplier_name'):
            supplier = Supplier.search([('name', '=', vals['supplier_name'])], limit=1)
            if not supplier:
                supplier = Supplier.create({'supplier_id': 0, 'name': vals['supplier_name']})
        vals['supplier_id'] = supplier.id if supplier else False

        variant = self.sudo().search([('article_id', '=', vals['article_id'])], limit=1)
        if not variant and vals.get('identity_key'):
            variant = self.sudo().search([('identity_key', '=', vals['identity_key'])], limit=1)
        if not variant and vals.get('article_no_key') and vals.get('supplier_external_id'):
            variant = self.sudo().search([
                ('article_no_key', '=', vals['article_no_key']),
                ('supplier_external_id', '=', vals['supplier_external_id']),
            ], limit=1)
        if variant:
            update_vals = {k: v for k, v in vals.items() if v not in (False, '', None)}
            update_vals.pop('product_tmpl_id', None)
            variant.write(update_vals)
            return variant
        vals['is_reference_only'] = True
        return self.sudo().create(vals)

    def action_create_product_from_reference(self):
        self.ensure_one()
        if self.product_tmpl_id:
            return _product_template_action(self.product_tmpl_id)

        api = self.env['tecdoc.api']._get_default_api()
        if not api:
            raise UserError("Please configure TecDoc API first!")

        article_id = self.article_id if self.article_id and self.article_id > 0 else None
        supplier_id = self.supplier_external_id or None
        try:
            product = api.sync_product_from_tecdoc(
                article_id=article_id,
                article_no=self.article_no,
                supplier_id=supplier_id,
            )
        except UserError:
            product = api.sync_product_from_article_snippet({
                'articleId': article_id,
                'articleNo': self.article_no,
                'supplierId': supplier_id,
                'supplierName': self.supplier_name,
                'articleProductName': self.article_product_name,
                's3image': self.image_url,
                'articleMediaFileName': self.media_filename,
                'articleMediaType': self.media_type,
            }, fallback_article_no=self.article_no)

        template = product.product_tmpl_id if product._name == 'product.product' else product
        if template:
            self.write({'product_tmpl_id': template.id, 'is_reference_only': False})
            return _product_template_action(template)
        return True

    def action_enrich_reference(self):
        self.ensure_one()
        api = self.env['tecdoc.api']._get_default_api()
        if not api:
            raise UserError("Please configure TecDoc API first!")
        api.enrich_tecdoc_variant_reference(self)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'TecDoc',
                'message': 'Reference enriched from TecDoc.',
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def action_open_vehicles(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Compatible Vehicles',
            'res_model': 'tecdoc.vehicle',
            'view_mode': 'list,form',
            'domain': [('variant_ids', '=', self.id)],
            'context': {'default_variant_ids': [(4, self.id)]},
        }

    def action_open_oem_numbers(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'OEM Numbers',
            'res_model': 'tecdoc.oem.number',
            'view_mode': 'list,form',
            'domain': [('variant_ids', '=', self.id)],
        }

    def action_open_eans(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'EANs',
            'res_model': 'tecdoc.article.variant.ean',
            'view_mode': 'list,form',
            'domain': [('variant_id', '=', self.id)],
        }

    def action_open_specs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Specifications',
            'res_model': 'tecdoc.article.variant.criteria',
            'view_mode': 'list,form',
            'domain': [('variant_id', '=', self.id)],
        }

    def action_open_cross_refs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Cross References',
            'res_model': 'tecdoc.article.variant.cross',
            'view_mode': 'list,form',
            'domain': [('variant_id', '=', self.id)],
        }


class TecDocArticleVariantEan(models.Model):
    _name = 'tecdoc.article.variant.ean'
    _description = 'TecDoc Article Variant EAN'
    _order = 'ean'

    variant_id = fields.Many2one('tecdoc.article.variant', required=True, index=True, ondelete='cascade')
    ean = fields.Char(required=True, index=True)
    ean_key = fields.Char(required=True, index=True)

    _sql_constraints = [
        ('tecdoc_variant_ean_unique', 'unique(variant_id, ean_key)', 'EAN must be unique per variant.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('ean_key') and vals.get('ean'):
                vals['ean_key'] = _normalize_key(vals.get('ean'))
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('ean') and not vals.get('ean_key'):
            vals['ean_key'] = _normalize_key(vals.get('ean'))
        return super().write(vals)


class TecDocArticleVariantCriteriaValue(models.Model):
    _name = 'tecdoc.article.variant.criteria'
    _description = 'TecDoc Article Variant Criteria Value'
    _order = 'criteria_id, id'

    variant_id = fields.Many2one('tecdoc.article.variant', required=True, index=True, ondelete='cascade')
    criteria_id = fields.Many2one('tecdoc.criteria', required=True, index=True, ondelete='restrict')
    value_text = fields.Char(index=True)

    _sql_constraints = [
        ('tecdoc_variant_criteria_unique', 'unique(variant_id, criteria_id, value_text)', 'Criteria value must be unique per variant.'),
    ]


class TecDocArticleVariantCross(models.Model):
    _name = 'tecdoc.article.variant.cross'
    _description = 'TecDoc Article Variant Cross Reference'
    _order = 'cross_number_id, id'

    variant_id = fields.Many2one('tecdoc.article.variant', required=True, index=True, ondelete='cascade')
    cross_number_id = fields.Many2one('tecdoc.cross.number', required=True, index=True, ondelete='restrict')

    search_level = fields.Char(index=True)
    source_supplier_id = fields.Many2one('tecdoc.supplier', index=True, ondelete='set null')
    source_supplier_name = fields.Char(index=True)

    article_brand_root = fields.Char(index=True)
    article_number_root = fields.Char(index=True)

    _sql_constraints = [
        ('tecdoc_variant_cross_unique', 'unique(variant_id, cross_number_id, search_level)', 'Cross reference must be unique per variant.'),
    ]


class TecDocArticleRelation(models.Model):
    _name = 'tecdoc.article.relation'
    _description = 'TecDoc Article Relation'
    _order = 'relation_type, target_variant_id'

    source_variant_id = fields.Many2one('tecdoc.article.variant', required=True, index=True, ondelete='cascade')
    target_variant_id = fields.Many2one('tecdoc.article.variant', required=True, index=True, ondelete='cascade')
    relation_type = fields.Selection(
        [
            ('am_equivalent', 'Echivalent AM'),
            ('oe_equivalent', 'Echivalent OE'),
            ('cross_ref', 'Cross Reference'),
        ],
        required=True,
        default='cross_ref',
        index=True,
    )
    source = fields.Char(index=True)
    confidence = fields.Float(default=80.0)

    _sql_constraints = [
        (
            'tecdoc_article_relation_unique',
            'unique(source_variant_id, target_variant_id, relation_type)',
            'Article relation must be unique.',
        ),
    ]

    @api.constrains('source_variant_id', 'target_variant_id')
    def _check_not_self(self):
        for rec in self:
            if rec.source_variant_id == rec.target_variant_id:
                raise ValidationError('Article relation cannot point to itself.')


class ProductTemplateTecDocFast(models.Model):
    _inherit = 'product.template'

    tecdoc_article_no_key = fields.Char(index=True)
    tecdoc_variant_ids = fields.One2many('tecdoc.article.variant', 'product_tmpl_id', string='TecDoc Variants')
    tecdoc_variant_count = fields.Integer(compute='_compute_tecdoc_variant_count', store=True)
    tecdoc_fast_managed = fields.Boolean(index=True, default=False)
    tecdoc_fast_last_import_at = fields.Datetime(index=True)

    tecdoc_lookup = fields.Char(
        string='TecDoc Lookup',
        help='Search helper: article no / OEM / EAN / cross number (exact).',
        search='_search_tecdoc_lookup',
    )

    @api.depends('tecdoc_variant_ids')
    def _compute_tecdoc_variant_count(self):
        for rec in self:
            rec.tecdoc_variant_count = len(rec.tecdoc_variant_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('tecdoc_article_no') and not vals.get('tecdoc_article_no_key'):
                vals['tecdoc_article_no_key'] = _normalize_key(vals.get('tecdoc_article_no'))
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('tecdoc_article_no') and not vals.get('tecdoc_article_no_key'):
            vals['tecdoc_article_no_key'] = _normalize_key(vals.get('tecdoc_article_no'))
        return super().write(vals)

    @api.model
    def _search_tecdoc_lookup(self, operator, value):
        if operator not in ('=', 'ilike', '=ilike', 'like'):
            return []
        if not value:
            return []
        key = _normalize_key(value)
        if not key:
            return []
        return [
            '|', '|', '|',
            ('tecdoc_article_no_key', '=', key),
            ('tecdoc_variant_ids.oem_number_ids.number_key', '=', key),
            ('tecdoc_variant_ids.ean_ids.ean_key', '=', key),
            ('tecdoc_variant_ids.cross_link_ids.cross_number_id.number_key', '=', key),
        ]

    def action_open_tecdoc_variants(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'TecDoc Variants',
            'res_model': 'tecdoc.article.variant',
            'view_mode': 'list,form',
            'domain': [('product_tmpl_id', '=', self.id)],
            'context': {'default_product_tmpl_id': self.id},
        }

    def action_open_tecdoc_vehicles(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Compatible Vehicles',
            'res_model': 'tecdoc.vehicle',
            'view_mode': 'list,form',
            'domain': [('variant_ids.product_tmpl_id', '=', self.id)],
        }

    def action_open_tecdoc_oem_numbers(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'OEM Numbers',
            'res_model': 'tecdoc.oem.number',
            'view_mode': 'list,form',
            'domain': [('variant_ids.product_tmpl_id', '=', self.id)],
        }

    def action_open_fast_vehicles(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Compatible Vehicles',
            'res_model': 'tecdoc.vehicle',
            'view_mode': 'list,form',
            'domain': [('variant_ids.product_tmpl_id', '=', self.id)],
            'context': {'group_by': 'manufacturer_name'},
        }

    def action_open_fast_oem_numbers(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'OEM Numbers',
            'res_model': 'tecdoc.oem.number',
            'view_mode': 'list,form',
            'domain': [('variant_ids.product_tmpl_id', '=', self.id)],
            'context': {'group_by': 'brand'},
        }

    def action_open_fast_cross_numbers(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Echivalente',
            'res_model': 'tecdoc.cross.number',
            'view_mode': 'list,form',
            'domain': [('cross_link_ids.variant_id.product_tmpl_id', '=', self.id)],
            'context': {'group_by': 'manufacturer'},
        }

    def action_open_fast_specs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Specifications',
            'res_model': 'tecdoc.article.variant.criteria',
            'view_mode': 'list,form',
            'domain': [('variant_id.product_tmpl_id', '=', self.id)],
            'context': {'group_by': 'criteria_id'},
        }


class TecDocArticleVariantRelFields(models.Model):
    _inherit = 'tecdoc.article.variant'

    ean_ids = fields.One2many('tecdoc.article.variant.ean', 'variant_id', string='EANs')
    criteria_value_ids = fields.One2many('tecdoc.article.variant.criteria', 'variant_id', string='Criteria Values')
    cross_link_ids = fields.One2many('tecdoc.article.variant.cross', 'variant_id', string='Cross References')
