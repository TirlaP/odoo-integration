# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import ValidationError, UserError


class ResPartner(models.Model):
    """Extended Customer/Partner model for automotive business"""
    _inherit = 'res.partner'

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

    # Romanian-specific fields
    client_type = fields.Selection([
        ('individual', 'Persoană Fizică'),
        ('company', 'Persoană Juridică'),
        ('mechanic', 'Mecanic'),
    ], string='Tip Client', default='individual')

    cui = fields.Char('CUI', help='Cod Unic de Înregistrare (pentru persoane juridice)')
    cnp = fields.Char('CNP', help='Cod Numeric Personal (pentru persoane fizice)')

    # Customer balance
    current_balance = fields.Monetary(
        'Sold Curent',
        compute='_compute_current_balance',
        currency_field='currency_id',
        store=False,
    )
    automotive_order_total = fields.Monetary(
        'Total Comenzi Auto',
        compute='_compute_automotive_financial_summary',
        currency_field='currency_id',
    )
    automotive_paid_total = fields.Monetary(
        'Total Plăți Alocate',
        compute='_compute_automotive_financial_summary',
        currency_field='currency_id',
    )
    automotive_refund_total = fields.Monetary(
        'Total Refund-uri',
        compute='_compute_automotive_financial_summary',
        currency_field='currency_id',
    )
    automotive_return_total = fields.Monetary(
        'Total Retururi',
        compute='_compute_automotive_financial_summary',
        currency_field='currency_id',
    )
    automotive_credit_adjustment_total = fields.Monetary(
        'Ajustări Credit',
        compute='_compute_automotive_financial_summary',
        currency_field='currency_id',
    )
    automotive_balance_due = fields.Monetary(
        'Sold Operațional',
        compute='_compute_automotive_financial_summary',
        currency_field='currency_id',
    )
    automotive_balance_formula = fields.Char(
        'Formula Sold Operațional',
        compute='_compute_automotive_financial_summary',
    )
    automotive_payment_count = fields.Integer(
        'Plăți Auto',
        compute='_compute_automotive_financial_summary',
    )

    # Mechanic portal access
    is_mechanic = fields.Boolean('Este Mecanic', compute='_compute_is_mechanic', store=True)
    mechanic_portal_user_id = fields.Many2one(
        'res.users',
        string='Utilizator portal mecanic',
        compute='_compute_mechanic_portal_access',
        compute_sudo=True,
    )
    mechanic_portal_access = fields.Boolean(
        'Acces portal mecanic',
        compute='_compute_mechanic_portal_access',
        compute_sudo=True,
    )

    # Audit fields
    create_uid_name = fields.Char('Created By', compute='_compute_audit_fields', store=True)
    write_uid_name = fields.Char('Last Modified By', compute='_compute_audit_fields', store=True)

    @api.depends('client_type')
    def _compute_is_mechanic(self):
        """Compute if partner is a mechanic"""
        for partner in self:
            partner.is_mechanic = partner.client_type == 'mechanic'

    @api.depends('user_ids.groups_id', 'client_type')
    def _compute_mechanic_portal_access(self):
        mechanic_group = self.env.ref('automotive_parts.group_mechanic_portal', raise_if_not_found=False)
        for partner in self:
            portal_user = partner.with_context(active_test=False).user_ids.filtered(
                lambda user: user._is_portal() and (
                    not mechanic_group or mechanic_group in user.groups_id
                )
            )[:1]
            partner.mechanic_portal_user_id = portal_user
            partner.mechanic_portal_access = bool(portal_user)

    @api.depends('credit', 'debit', 'commercial_partner_id.credit', 'company_id')
    def _compute_current_balance(self):
        """Compute current balance from the accounting receivable position."""
        for partner in self:
            accounting_partner = partner.commercial_partner_id.sudo().with_company(partner.company_id or self.env.company)
            partner.current_balance = accounting_partner.credit

    def _get_automotive_order_domain(self):
        self.ensure_one()
        commercial_partner = self.commercial_partner_id
        base_domain = [
            ('company_id', '=', (self.company_id or self.env.company).id),
            ('state', 'in', ['sale', 'done']),
            ('auto_state', '!=', 'cancel'),
        ]
        return base_domain + [
            '|',
            ('partner_id', 'child_of', [commercial_partner.id]),
            ('mechanic_partner_id', 'child_of', [commercial_partner.id]),
        ]

    def _get_automotive_allocation_domain(self):
        self.ensure_one()
        commercial_partner = self.commercial_partner_id
        return [
            ('company_id', '=', (self.company_id or self.env.company).id),
            ('partner_id', 'child_of', [commercial_partner.id]),
            ('active', '=', True),
            ('payment_state', '=', 'paid'),
        ]

    def _compute_automotive_financial_summary(self):
        SaleOrder = self.env['sale.order']
        Allocation = self.env['automotive.payment.allocation']
        for partner in self:
            orders = SaleOrder.search(partner._get_automotive_order_domain())
            allocations = Allocation.search(partner._get_automotive_allocation_domain())
            inbound_allocations = allocations.filtered(lambda allocation: allocation.payment_type == 'inbound')
            partner.automotive_order_total = sum(orders.mapped('amount_total'))
            partner.automotive_paid_total = sum(inbound_allocations.mapped('amount'))
            partner.automotive_refund_total = sum(orders.mapped('automotive_refund_amount'))
            partner.automotive_return_total = sum(orders.mapped('automotive_return_amount'))
            partner.automotive_credit_adjustment_total = sum(orders.mapped('automotive_credit_adjustment_total'))
            partner.automotive_balance_due = (
                partner.automotive_order_total
                - partner.automotive_paid_total
                - partner.automotive_credit_adjustment_total
            )
            partner.automotive_balance_formula = (
                'Sold operațional = Total comenzi - Plăți încasate - Ajustări retur/refund '
                '(ajustarea folosește valoarea confirmată cea mai mare dintre retururile operaționale și documentele de credit)'
            )
            partner.automotive_payment_count = len(allocations.mapped('payment_id'))

    @api.depends('create_uid', 'write_uid')
    def _compute_audit_fields(self):
        """Compute audit trail fields"""
        for partner in self:
            partner.create_uid_name = partner.create_uid.name if partner.create_uid else ''
            partner.write_uid_name = partner.write_uid.name if partner.write_uid else ''

    @api.constrains('cui')
    def _check_cui(self):
        """Validate CUI format (basic validation)"""
        for partner in self:
            if partner.cui and partner.client_type == 'company':
                cui = partner.cui.replace('RO', '').strip()
                if not cui.isdigit():
                    raise ValidationError('CUI trebuie să conțină doar cifre (opțional prefixat cu RO)')
                if len(cui) < 2 or len(cui) > 10:
                    raise ValidationError('CUI trebuie să aibă între 2 și 10 cifre')

    @api.constrains('cnp')
    def _check_cnp(self):
        """Validate CNP format (basic validation)"""
        for partner in self:
            if partner.cnp and partner.client_type == 'individual':
                if not partner.cnp.isdigit():
                    raise ValidationError('CNP trebuie să conțină doar cifre')
                if len(partner.cnp) != 13:
                    raise ValidationError('CNP trebuie să aibă exact 13 cifre')

    @api.model_create_multi
    def create(self, vals_list):
        """Override create to log creation"""
        partners = super().create(vals_list)

        audit_log = self.env['automotive.audit.log']
        for partner, vals in zip(partners, vals_list):
            tracked_fields = [f for f in vals.keys() if f in partner._fields]
            audit_log.log_change(
                action='create',
                record=partner,
                description=f'Created customer: {partner.name}',
                new_values=partner._audit_snapshot(tracked_fields),
            )

        partners.filtered(lambda partner: partner.is_mechanic)._sync_mechanic_portal_users()
        return partners

    def write(self, vals):
        """Override write to log modifications"""
        tracked_fields = [f for f in vals.keys() if f in self._fields]
        old_by_id = {partner.id: partner._audit_snapshot(tracked_fields) for partner in self}

        result = super().write(vals)

        audit_log = self.env['automotive.audit.log']
        for partner in self:
            audit_log.log_change(
                action='write',
                record=partner,
                description=f'Modified customer: {partner.name}',
                old_values=old_by_id.get(partner.id),
                new_values=partner._audit_snapshot(tracked_fields),
            )

        if 'client_type' in vals:
            self._sync_mechanic_portal_users()

        return result

    def unlink(self):
        SaleOrder = self.env['sale.order']
        for partner in self:
            if SaleOrder.search_count([('partner_id', '=', partner.id)]):
                raise UserError('Nu poți șterge un client care are comenzi asociate. Folosește arhivarea (Deactivate).')
        audit_log = self.env['automotive.audit.log']
        for partner in self:
            audit_log.log_change(
                action='unlink',
                record=partner,
                description=f'Deleted customer: {partner.name}',
                old_values=partner._audit_snapshot(['name', 'client_type', 'cui', 'cnp', 'active']),
            )
        return super().unlink()

    def action_view_orders(self):
        """View customer orders"""
        self.ensure_one()
        return {
            'name': 'Comenzi',
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id)],
            'context': {'default_partner_id': self.id}
        }

    def action_view_invoices(self):
        """View customer invoices"""
        self.ensure_one()
        return {
            'name': 'Facturi',
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id), ('move_type', '=', 'out_invoice')],
            'context': {'default_partner_id': self.id}
        }

    def action_view_automotive_payment_allocations(self):
        self.ensure_one()
        return {
            'name': 'Automotive Payment Allocations',
            'type': 'ir.actions.act_window',
            'res_model': 'automotive.payment.allocation',
            'view_mode': 'list,form',
            'domain': self._get_automotive_allocation_domain(),
            'context': {
                'search_default_group_partner': 1,
                'default_partner_id': self.commercial_partner_id.id,
            },
        }

    def action_open_mechanic_portal_wizard(self):
        """Open the standard portal wizard for mechanic access management."""
        self.ensure_one()
        return self.env['portal.wizard'].with_context(active_ids=self.ids).action_open_wizard()

    def _sync_mechanic_portal_users(self):
        """Keep the mechanic portal group aligned with the partner type."""
        group_mechanic = self.env.ref('automotive_parts.group_mechanic_portal', raise_if_not_found=False)
        if not group_mechanic:
            return

        for partner in self:
            users = partner.with_context(active_test=False).user_ids.sudo()
            if not users:
                continue

            if partner.is_mechanic:
                for user in users:
                    if group_mechanic not in user.groups_id:
                        user.write({'groups_id': [(4, group_mechanic.id)]})
            else:
                for user in users.filtered(lambda user: group_mechanic in user.groups_id):
                    user.write({'groups_id': [(3, group_mechanic.id)]})


class PortalWizardUser(models.TransientModel):
    """Add mechanic-specific portal access to standard portal invitations."""
    _inherit = 'portal.wizard.user'

    def _sync_mechanic_portal_group(self):
        group_mechanic = self.env.ref('automotive_parts.group_mechanic_portal', raise_if_not_found=False)
        if not group_mechanic:
            return

        for wizard_user in self:
            user = wizard_user.user_id.sudo()
            if not user:
                continue

            if wizard_user.partner_id.client_type == 'mechanic':
                if group_mechanic not in user.groups_id:
                    user.write({'groups_id': [(4, group_mechanic.id)]})
            elif group_mechanic in user.groups_id:
                user.write({'groups_id': [(3, group_mechanic.id)]})

    def action_grant_access(self):
        result = super().action_grant_access()
        self._sync_mechanic_portal_group()
        return result

    def action_revoke_access(self):
        result = super().action_revoke_access()
        self._sync_mechanic_portal_group()
        return result
