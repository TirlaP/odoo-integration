# -*- coding: utf-8 -*-
from odoo import fields, models
from odoo.exceptions import UserError


class TecDocFastPurgeWizard(models.TransientModel):
    _name = 'tecdoc.fast.purge.wizard'
    _description = 'TecDoc Fast Purge Wizard'

    run_id = fields.Many2one('tecdoc.fast.import.run', string='Import Run')

    confirmation = fields.Char(
        required=True,
        help='Type DELETE to confirm destructive purge.',
    )

    purge_fast_tables = fields.Boolean(
        default=True,
        help='Delete TecDoc Fast tables (variants, OEM, vehicles, specs, cross refs, suppliers).',
    )

    product_scope = fields.Selection(
        [
            ('fast', 'Only TecDoc Fast Managed'),
            ('any_tecdoc', 'Any Product With TecDoc Fields'),
        ],
        default='fast',
        required=True,
        help="Controls which products are affected by Product Action. "
             "'Any Product With TecDoc Fields' includes legacy TecDoc-synced products too.",
    )

    product_action = fields.Selection(
        [
            ('keep', 'Keep Products'),
            ('archive', 'Archive Products (recommended)'),
            ('delete', 'Delete Products'),
        ],
        default='archive',
        required=True,
        help='Action for product.template records marked as TecDoc Fast managed.',
    )

    def action_confirm(self):
        self.ensure_one()
        if (self.confirmation or '').strip().upper() != 'DELETE':
            raise UserError('Type DELETE in the confirmation field to proceed.')

        env = self.env
        audit_target = self.run_id or env.company
        summary = {
            'purge_fast_tables': self.purge_fast_tables,
            'product_scope': self.product_scope,
            'product_action': self.product_action,
        }

        if self.purge_fast_tables:
            summary.update({
                'variants_before': env['tecdoc.article.variant'].sudo().search_count([]),
                'vehicles_before': env['tecdoc.vehicle'].sudo().search_count([]),
                'oem_numbers_before': env['tecdoc.oem.number'].sudo().search_count([]),
                'cross_numbers_before': env['tecdoc.cross.number'].sudo().search_count([]),
                'criteria_before': env['tecdoc.criteria'].sudo().search_count([]),
                'suppliers_before': env['tecdoc.supplier'].sudo().search_count([]),
            })

        # First remove variants (cascades EAN/spec/cross rows and clears M2M rel tables)
        if self.purge_fast_tables:
            env['tecdoc.article.variant'].sudo().search([]).unlink()

            # Clear lookup tables (now safe because no variants reference them)
            env['tecdoc.vehicle'].sudo().search([]).unlink()
            env['tecdoc.oem.number'].sudo().search([]).unlink()
            env['tecdoc.cross.number'].sudo().search([]).unlink()
            env['tecdoc.criteria'].sudo().search([]).unlink()
            env['tecdoc.supplier'].sudo().search([]).unlink()

        product_domain = []
        if self.product_scope == 'any_tecdoc':
            product_domain = ['|', ('tecdoc_id', '!=', False), ('tecdoc_article_no', '!=', False)]
        else:
            product_domain = [('tecdoc_fast_managed', '=', True)]

        products = env['product.template'].sudo().with_context(skip_audit_log=True).search(product_domain)
        summary['products_before'] = len(products)
        if products:
            if self.product_action == 'keep':
                products.write({'tecdoc_fast_managed': False})
            elif self.product_action == 'archive':
                products.write({'active': False})
                products.write({'tecdoc_fast_managed': False})
            elif self.product_action == 'delete':
                try:
                    products.unlink()
                except Exception as exc:
                    raise UserError(
                        f"Could not delete products (they may be referenced by documents). "
                        f"Choose 'Archive' instead.\n\nError: {exc}"
                    )

        env['automotive.audit.log'].log_change(
            action='custom',
            record=audit_target,
            description='TecDoc Fast purge executed',
            new_values=summary,
        )

        return {'type': 'ir.actions.act_window_close'}
