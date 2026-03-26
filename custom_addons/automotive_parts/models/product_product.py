# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare


class ProductProduct(models.Model):
    """Extended Product model for automotive parts"""
    _inherit = 'product.product'

    # TecDoc Integration (stored on product.template; mirrored here for convenience)
    tecdoc_id = fields.Char(related='product_tmpl_id.tecdoc_id', store=True, readonly=False, index=True)
    tecdoc_article_no = fields.Char(related='product_tmpl_id.tecdoc_article_no', store=True, readonly=False, index=True)
    tecdoc_supplier_id = fields.Integer(related='product_tmpl_id.tecdoc_supplier_id', store=True, readonly=False)
    tecdoc_supplier_name = fields.Char(related='product_tmpl_id.tecdoc_supplier_name', store=True, readonly=False)
    tecdoc_compatibility = fields.Text(related='product_tmpl_id.tecdoc_compatibility', store=True, readonly=False)
    tecdoc_ean = fields.Char(related='product_tmpl_id.tecdoc_ean', store=True, readonly=False)
    tecdoc_oem_numbers = fields.Text(related='product_tmpl_id.tecdoc_oem_numbers', store=True, readonly=False)
    tecdoc_specifications = fields.Text(related='product_tmpl_id.tecdoc_specifications', store=True, readonly=False)
    tecdoc_image_url = fields.Char(related='product_tmpl_id.tecdoc_image_url', store=True, readonly=False)
    tecdoc_media_filename = fields.Char(related='product_tmpl_id.tecdoc_media_filename', store=True, readonly=False)
    tecdoc_media_type = fields.Char(related='product_tmpl_id.tecdoc_media_type', store=True, readonly=False)

    # Automotive-specific fields
    supplier_code = fields.Char('Cod Furnizor', help='Supplier Part Number')
    barcode_internal = fields.Char('Cod de Bare Intern')
    is_automotive_part = fields.Boolean('Este Piesă Auto', default=True)

    # Stock and availability
    stock_available = fields.Float('Stoc Disponibil', compute='_compute_stock_available', store=True)
    stock_reserved = fields.Float('Stoc Rezervat', compute='_compute_stock_reserved', store=True)

    # Supplier information
    main_supplier_id = fields.Many2one('res.partner', 'Furnizor Principal',
                                        domain=[('is_company', '=', True)])

    # Stock alerts / refill reminders
    stock_alert_enabled = fields.Boolean(
        'Alerte refill',
        default=False,
        help='Enable a managed replenishment rule for the default warehouse stock location.',
    )
    stock_alert_min_qty = fields.Float(
        'Min alerta',
        digits='Product Unit of Measure',
        help='Minimum quantity that should remain available before the refill alert kicks in.',
    )
    stock_alert_target_qty = fields.Float(
        'Target alerta',
        digits='Product Unit of Measure',
        help='Target stock level to restore through the linked replenishment rule.',
    )
    stock_alert_warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Depozit implicit',
        compute='_compute_stock_alert_data',
        readonly=True,
    )
    stock_alert_location_id = fields.Many2one(
        'stock.location',
        string='Locatie stoc',
        compute='_compute_stock_alert_data',
        readonly=True,
    )
    stock_alert_orderpoint_id = fields.Many2one(
        'stock.warehouse.orderpoint',
        string='Replenishment rule',
        compute='_compute_stock_alert_data',
        readonly=True,
    )
    stock_alert_qty_to_order = fields.Float(
        'De comandat',
        digits='Product Unit of Measure',
        compute='_compute_stock_alert_data',
        readonly=True,
    )
    stock_alert_state = fields.Selection(
        [
            ('disabled', 'Disabled'),
            ('inactive_product', 'Product inactive'),
            ('no_warehouse', 'No default warehouse'),
            ('missing', 'Needs sync'),
            ('archived', 'Archived'),
            ('active', 'Active'),
            ('needs_reorder', 'Needs reorder'),
        ],
        string='Alert state',
        compute='_compute_stock_alert_data',
        readonly=True,
    )

    _AUDIT_FIELDS = {
        'active',
        'name',
        'default_code',
        'barcode',
        'barcode_internal',
        'supplier_code',
        'stock_alert_enabled',
        'stock_alert_min_qty',
        'stock_alert_target_qty',
        'list_price',
        'standard_price',
        'main_supplier_id',
    }

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

    @api.model_create_multi
    def create(self, vals_list):
        products = super().create(vals_list)
        if self.env.context.get('skip_audit_log') is True:
            if any(any(field in vals for field in {'active', 'company_id', 'stock_alert_enabled', 'stock_alert_min_qty', 'stock_alert_target_qty'}) for vals in vals_list):
                products._stock_alert_sync_managed_orderpoint()
            return products

        audit_log = self.env['automotive.audit.log']
        for product, vals in zip(products, vals_list):
            tracked_fields = [f for f in vals.keys() if f in self._AUDIT_FIELDS and f in product._fields]
            if not tracked_fields:
                continue
            audit_log.log_change(
                action='create',
                record=product,
                description=f'Created product variant: {product.display_name}',
                new_values=product._audit_snapshot(tracked_fields),
            )
        if any(any(field in vals for field in {'active', 'company_id', 'stock_alert_enabled', 'stock_alert_min_qty', 'stock_alert_target_qty'}) for vals in vals_list):
            products._stock_alert_sync_managed_orderpoint()
        return products

    def write(self, vals):
        context = dict(self.env.context or {})
        if context.get('skip_audit_log') is True:
            result = super().write(vals)
            if any(field in vals for field in {'active', 'company_id', 'stock_alert_enabled', 'stock_alert_min_qty', 'stock_alert_target_qty'}):
                self._stock_alert_sync_managed_orderpoint()
            return result

        tracked_fields = [f for f in vals.keys() if f in self._AUDIT_FIELDS and f in self._fields]
        old_by_id = {}
        if tracked_fields:
            old_by_id = {p.id: p._audit_snapshot(tracked_fields) for p in self}

        result = super().write(vals)

        if tracked_fields:
            audit_log = self.env['automotive.audit.log']
            for product in self:
                audit_log.log_change(
                    action='write',
                    record=product,
                    description=f'Modified product variant: {product.display_name}',
                    old_values=old_by_id.get(product.id),
                    new_values=product._audit_snapshot(tracked_fields),
                )
        if any(field in vals for field in {'active', 'company_id', 'stock_alert_enabled', 'stock_alert_min_qty', 'stock_alert_target_qty'}):
            self._stock_alert_sync_managed_orderpoint()
        return result

    @api.depends('qty_available', 'outgoing_qty')
    def _compute_stock_available(self):
        """Compute available stock"""
        for product in self:
            # Available = On Hand - Reserved
            product.stock_available = product.qty_available - product.outgoing_qty

    @api.depends('outgoing_qty')
    def _compute_stock_reserved(self):
        """Compute reserved stock from active orders"""
        for product in self:
            # Use Odoo's built-in outgoing_qty which tracks reserved stock
            product.stock_reserved = product.outgoing_qty

    def _stock_alert_get_company(self):
        self.ensure_one()
        return self.company_id or self.product_tmpl_id.company_id or self.env.company

    def _stock_alert_get_default_warehouse(self):
        self.ensure_one()
        company = self._stock_alert_get_company()
        return self.env['stock.warehouse'].sudo().search(
            [('company_id', '=', company.id)],
            order='sequence,id',
            limit=1,
        )

    def _stock_alert_get_managed_orderpoints(self):
        self.ensure_one()
        return self.env['stock.warehouse.orderpoint'].sudo().with_context(active_test=False).search([
            ('product_id', '=', self.id),
            ('stock_alert_managed', '=', True),
        ])

    def _stock_alert_get_current_orderpoint(self):
        self.ensure_one()
        warehouse = self._stock_alert_get_default_warehouse()
        if not warehouse:
            return self.env['stock.warehouse.orderpoint']
        company = self._stock_alert_get_company()
        return self.env['stock.warehouse.orderpoint'].sudo().with_context(active_test=False).search([
            ('product_id', '=', self.id),
            ('location_id', '=', warehouse.lot_stock_id.id),
            ('company_id', '=', company.id),
        ], limit=1)

    @api.depends(
        'stock_alert_enabled',
        'stock_alert_min_qty',
        'stock_alert_target_qty',
        'active',
        'company_id',
        'product_tmpl_id.company_id',
    )
    def _compute_stock_alert_data(self):
        Orderpoint = self.env['stock.warehouse.orderpoint'].sudo().with_context(active_test=False)
        for product in self:
            warehouse = product._stock_alert_get_default_warehouse()
            location = warehouse.lot_stock_id if warehouse else False
            orderpoint = False
            if warehouse:
                company = product._stock_alert_get_company()
                orderpoint = Orderpoint.search([
                    ('product_id', '=', product.id),
                    ('location_id', '=', location.id),
                    ('company_id', '=', company.id),
                ], limit=1)

            product.stock_alert_warehouse_id = warehouse
            product.stock_alert_location_id = location
            product.stock_alert_orderpoint_id = orderpoint
            product.stock_alert_qty_to_order = orderpoint.qty_to_order if orderpoint else 0.0

            if not product.stock_alert_enabled:
                product.stock_alert_state = 'disabled'
            elif not product.active:
                product.stock_alert_state = 'inactive_product'
            elif not warehouse:
                product.stock_alert_state = 'no_warehouse'
            elif not orderpoint:
                product.stock_alert_state = 'missing'
            elif not orderpoint.active:
                product.stock_alert_state = 'archived'
            elif float_compare(orderpoint.qty_to_order, 0.0, precision_rounding=orderpoint.product_uom.rounding) > 0:
                product.stock_alert_state = 'needs_reorder'
            else:
                product.stock_alert_state = 'active'

    @api.constrains('stock_alert_min_qty', 'stock_alert_target_qty')
    def _check_stock_alert_qtys(self):
        for product in self:
            if float_compare(product.stock_alert_target_qty, product.stock_alert_min_qty, precision_rounding=product.uom_id.rounding) < 0:
                raise ValidationError(_('The refill target quantity must be greater than or equal to the minimum quantity.'))

    def _stock_alert_sync_managed_orderpoint(self):
        Orderpoint = self.env['stock.warehouse.orderpoint'].sudo().with_context(active_test=False)
        for product in self:
            managed_orderpoints = product._stock_alert_get_managed_orderpoints()
            warehouse = product._stock_alert_get_default_warehouse()
            company = product._stock_alert_get_company()
            current_orderpoint = product._stock_alert_get_current_orderpoint()

            should_enable = bool(product.stock_alert_enabled and product.active and warehouse)
            if should_enable:
                orderpoint_values = {
                    'product_id': product.id,
                    'warehouse_id': warehouse.id,
                    'location_id': warehouse.lot_stock_id.id,
                    'company_id': company.id,
                    'trigger': 'manual',
                    'active': True,
                    'stock_alert_managed': True,
                    'product_min_qty': product.stock_alert_min_qty,
                    'product_max_qty': max(product.stock_alert_min_qty, product.stock_alert_target_qty),
                }
                if current_orderpoint:
                    current_orderpoint.write(orderpoint_values)
                else:
                    current_orderpoint = Orderpoint.create(orderpoint_values)

                if managed_orderpoints:
                    (managed_orderpoints - current_orderpoint).write({'active': False})
            elif managed_orderpoints:
                managed_orderpoints.write({'active': False})

    def action_view_stock_alert_orderpoint(self):
        self.ensure_one()
        action = self.env['ir.actions.actions']._for_xml_id('stock.action_orderpoint')
        action['context'] = dict(action.get('context') or {})
        action['context'].update({
            'active_test': False,
            'search_default_filter_not_snoozed': True,
            'search_default_product_id': self.id,
            'default_product_id': self.id,
        })
        current_orderpoint = self._stock_alert_get_current_orderpoint()
        if current_orderpoint:
            action['domain'] = [('id', '=', current_orderpoint.id)]
            action['res_id'] = current_orderpoint.id
        else:
            managed_orderpoints = self._stock_alert_get_managed_orderpoints()
            if managed_orderpoints:
                action['domain'] = [('id', 'in', managed_orderpoints.ids)]
                if len(managed_orderpoints) == 1:
                    action['res_id'] = managed_orderpoints.id
            else:
                action['domain'] = [('product_id', '=', self.id), ('stock_alert_managed', '=', True)]
        return action

    def action_sync_stock_alert_orderpoint(self):
        self.ensure_one()
        self._stock_alert_sync_managed_orderpoint()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Stock alert synchronized'),
                'message': _('The managed replenishment rule was updated against the default warehouse stock location.'),
                'type': 'success',
                'sticky': False,
            }
        }

    def action_sync_from_tecdoc(self):
        """Sync product data from TecDoc"""
        self.ensure_one()

        if not (self.tecdoc_id or self.tecdoc_article_no or self.default_code):
            raise UserError('This product has no TecDoc ID or Article Number!')

        api = self.env['tecdoc.api'].search([], limit=1)

        if not api:
            raise UserError('TecDoc API not configured!')

        # Prefer syncing by article number (stable), fall back to TecDoc ID if needed.
        article_data = None
        article_no = self.tecdoc_article_no or self.default_code
        if article_no:
            try:
                article_data = api._extract_article(api.get_article_details_by_number_typed(article_no))
            except UserError:
                try:
                    article_data = api._extract_article(api.get_article_details_by_number(article_no))
                except UserError:
                    article_data = None
        if not article_data and self.tecdoc_id:
            article_data = api._extract_article(api.get_article_details(self.tecdoc_id))

        if article_data:
            resolved_id = article_data.get('articleId') or article_data.get('article_id') or self.tecdoc_id
            # Get image URL from article data
            image_url = (
                article_data.get('s3image')
                or article_data.get('imageUrl')
                or article_data.get('image_url')
            )
            # Update product with fresh data
            vals = {
                'name': (
                    article_data.get('articleName')
                    or article_data.get('articleProductName')
                    or article_data.get('articleProductNameLong')
                    or article_data.get('genericArticleName')
                    or self.name
                ),
                'tecdoc_article_no': article_data.get('articleNo'),
                'description': article_data.get('description', ''),
                'tecdoc_image_url': image_url,
            }
            if resolved_id:
                vals['tecdoc_id'] = str(resolved_id)
            self.write(vals)

            # Sync image to product template's image_1920 field
            template = self.product_tmpl_id
            should_download = api.download_images and (api.overwrite_images or not template.image_1920)
            if should_download and image_url:
                image_b64 = api._fetch_image_base64(image_url)
                if image_b64:
                    try:
                        template.write({'image_1920': image_b64})
                    except Exception:
                        pass  # Logged inside _fetch_image_base64

            # Sync compatibility
            api._sync_vehicle_compatibility(self, self.tecdoc_id)

        return True

    def action_view_compatible_vehicles(self):
        """View compatible vehicles for this part"""
        self.ensure_one()

        product = self.product_tmpl_id
        variants = product.tecdoc_variant_ids
        if not variants:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Compatible Vehicles',
                    'message': 'No preloaded compatibility found in DB. Run TecDoc → Fast Import first.',
                    'type': 'warning',
                    'sticky': False,
                }
            }

        return {
            'type': 'ir.actions.act_window',
            'name': 'Compatible Vehicles',
            'res_model': 'tecdoc.vehicle',
            'view_mode': 'list,form',
            'domain': [('variant_ids.product_tmpl_id', '=', product.id)],
            'context': {'group_by': 'manufacturer_name'},
        }

    def action_generate_label(self):
        """Generate product label for printing"""
        self.ensure_one()

        # This is a placeholder - integrate with your label printer
        # For now, just return product info
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Label Ready',
                'message': f'Label for {self.name} (Barcode: {self.barcode or "N/A"})',
                'type': 'success',
                'sticky': False,
            }
        }


class ProductTemplate(models.Model):
    """Extended Product Template"""
    _inherit = 'product.template'

    # Add template-level fields if needed
    tecdoc_category = fields.Char('TecDoc Category')

    # TecDoc Integration (template-level: this is the main product form in Odoo)
    tecdoc_id = fields.Char('TecDoc ID', index=True, help='TecDoc Article ID')
    tecdoc_article_no = fields.Char('TecDoc Article No', index=True)
    tecdoc_supplier_id = fields.Integer('TecDoc Supplier ID')
    tecdoc_supplier_name = fields.Char('TecDoc Supplier Name')
    tecdoc_compatibility = fields.Text('Vehicle Compatibility', help='Compatible vehicles from TecDoc')
    tecdoc_ean = fields.Char('TecDoc EAN')
    tecdoc_oem_numbers = fields.Text('TecDoc OEM Numbers')
    tecdoc_specifications = fields.Text('TecDoc Specifications')
    tecdoc_image_url = fields.Char('TecDoc Image URL')
    tecdoc_media_filename = fields.Char('TecDoc Media Filename')
    tecdoc_media_type = fields.Char('TecDoc Media Type')
    barcode_internal = fields.Char(
        related='product_variant_id.barcode_internal',
        readonly=False,
        string='Cod de Bare Intern',
    )
    supplier_code = fields.Char(
        related='product_variant_id.supplier_code',
        readonly=False,
        string='Cod Furnizor',
    )
    main_supplier_id = fields.Many2one(
        related='product_variant_id.main_supplier_id',
        readonly=False,
        string='Furnizor Principal',
    )
    stock_alert_enabled = fields.Boolean(
        related='product_variant_id.stock_alert_enabled',
        readonly=False,
        string='Alerte refill',
    )
    stock_alert_min_qty = fields.Float(
        related='product_variant_id.stock_alert_min_qty',
        readonly=False,
        string='Min alerta',
    )
    stock_alert_target_qty = fields.Float(
        related='product_variant_id.stock_alert_target_qty',
        readonly=False,
        string='Target alerta',
    )
    stock_alert_warehouse_id = fields.Many2one(
        related='product_variant_id.stock_alert_warehouse_id',
        readonly=True,
        string='Depozit implicit',
    )
    stock_alert_location_id = fields.Many2one(
        related='product_variant_id.stock_alert_location_id',
        readonly=True,
        string='Locatie stoc',
    )
    stock_alert_orderpoint_id = fields.Many2one(
        related='product_variant_id.stock_alert_orderpoint_id',
        readonly=True,
        string='Replenishment rule',
    )
    stock_alert_qty_to_order = fields.Float(
        related='product_variant_id.stock_alert_qty_to_order',
        readonly=True,
        string='De comandat',
    )
    stock_alert_state = fields.Selection(
        related='product_variant_id.stock_alert_state',
        readonly=True,
        string='Alert state',
    )
    stock_available = fields.Float(
        related='product_variant_id.stock_available',
        readonly=True,
        string='Stoc Disponibil',
    )
    stock_reserved = fields.Float(
        related='product_variant_id.stock_reserved',
        readonly=True,
        string='Stoc Rezervat',
    )

    _AUDIT_FIELDS = {
        'active',
        'name',
        'default_code',
        'barcode',
        'list_price',
        'standard_price',
        'categ_id',
        'uom_id',
        'uom_po_id',
        'tecdoc_article_no',
        'tecdoc_supplier_name',
        'tecdoc_ean',
        'tecdoc_image_url',
        'stock_alert_enabled',
        'stock_alert_min_qty',
        'stock_alert_target_qty',
    }

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

    @api.model_create_multi
    def create(self, vals_list):
        templates = super().create(vals_list)
        if self.env.context.get('skip_audit_log') is True:
            if any(any(field in vals for field in {'active', 'company_id', 'stock_alert_enabled', 'stock_alert_min_qty', 'stock_alert_target_qty'}) for vals in vals_list):
                templates.mapped('product_variant_id')._stock_alert_sync_managed_orderpoint()
            return templates

        audit_log = self.env['automotive.audit.log']
        for template, vals in zip(templates, vals_list):
            tracked_fields = [f for f in vals.keys() if f in self._AUDIT_FIELDS and f in template._fields]
            if not tracked_fields:
                continue
            audit_log.log_change(
                action='create',
                record=template,
                description=f'Created product template: {template.display_name}',
                new_values=template._audit_snapshot(tracked_fields),
            )
        if any(any(field in vals for field in {'active', 'company_id', 'stock_alert_enabled', 'stock_alert_min_qty', 'stock_alert_target_qty'}) for vals in vals_list):
            templates.mapped('product_variant_id')._stock_alert_sync_managed_orderpoint()
        return templates

    def write(self, vals):
        context = dict(self.env.context or {})
        if context.get('skip_audit_log') is True:
            result = super().write(vals)
            if any(field in vals for field in {'active', 'company_id', 'stock_alert_enabled', 'stock_alert_min_qty', 'stock_alert_target_qty'}):
                self.mapped('product_variant_id')._stock_alert_sync_managed_orderpoint()
            return result

        tracked_fields = [f for f in vals.keys() if f in self._AUDIT_FIELDS and f in self._fields]
        old_by_id = {}
        if tracked_fields:
            old_by_id = {t.id: t._audit_snapshot(tracked_fields) for t in self}

        result = super().write(vals)

        if tracked_fields:
            audit_log = self.env['automotive.audit.log']
            for template in self:
                audit_log.log_change(
                    action='write',
                    record=template,
                    description=f'Modified product template: {template.display_name}',
                    old_values=old_by_id.get(template.id),
                    new_values=template._audit_snapshot(tracked_fields),
                )
        if any(field in vals for field in {'active', 'company_id', 'stock_alert_enabled', 'stock_alert_min_qty', 'stock_alert_target_qty'}):
            self.mapped('product_variant_id')._stock_alert_sync_managed_orderpoint()
        return result

    def action_sync_from_tecdoc(self):
        self.ensure_one()
        return self.product_variant_id.action_sync_from_tecdoc()

    def action_view_compatible_vehicles(self):
        self.ensure_one()
        return self.product_variant_id.action_view_compatible_vehicles()

    def action_generate_label(self):
        self.ensure_one()
        return self.product_variant_id.action_generate_label()

    def action_view_stock_alert_orderpoint(self):
        self.ensure_one()
        return self.product_variant_id.action_view_stock_alert_orderpoint()

    def action_sync_stock_alert_orderpoint(self):
        self.ensure_one()
        return self.product_variant_id.action_sync_stock_alert_orderpoint()


class StockWarehouseOrderpoint(models.Model):
    _inherit = 'stock.warehouse.orderpoint'

    stock_alert_managed = fields.Boolean(
        'Managed by stock alert',
        default=False,
        index=True,
        help='Marks replenishment rules created and maintained by the automotive stock alert flow.',
    )
