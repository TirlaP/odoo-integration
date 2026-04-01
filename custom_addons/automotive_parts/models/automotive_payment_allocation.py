# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare


class AutomotivePaymentAllocation(models.Model):
    _name = 'automotive.payment.allocation'
    _description = 'Automotive Payment Allocation'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'payment_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Allocation',
        compute='_compute_name',
        store=True,
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company',
        required=True,
        index=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        'res.currency',
        related='payment_id.currency_id',
        store=True,
        readonly=True,
    )
    payment_id = fields.Many2one(
        'account.payment',
        string='Payment',
        required=True,
        index=True,
        ondelete='cascade',
        check_company=True,
        tracking=True,
    )
    payment_state = fields.Selection(
        related='payment_id.state',
        string='Payment State',
        store=True,
        readonly=True,
    )
    payment_date = fields.Date(
        related='payment_id.date',
        string='Payment Date',
        store=True,
        readonly=True,
    )
    payment_name = fields.Char(
        related='payment_id.name',
        string='Payment Reference',
        store=True,
        readonly=True,
    )
    payment_type = fields.Selection(
        related='payment_id.payment_type',
        string='Payment Type',
        store=True,
        readonly=True,
    )
    billing_partner_id = fields.Many2one(
        'res.partner',
        related='payment_id.partner_id',
        string='Billing Partner',
        store=True,
        readonly=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Customer / Mechanic',
        compute='_compute_partner_id',
        store=True,
        readonly=True,
        tracking=True,
    )
    sale_order_id = fields.Many2one(
        'sale.order',
        string='Order',
        required=True,
        index=True,
        check_company=True,
        tracking=True,
    )
    sale_order_line_id = fields.Many2one(
        'sale.order.line',
        string='Order Line',
        domain="[('order_id', '=', sale_order_id)]",
        tracking=True,
    )
    account_move_id = fields.Many2one(
        'account.move',
        string='Invoice / Refund',
        domain="[('move_type', 'in', ['out_invoice', 'out_refund', 'out_receipt']), ('partner_id', 'child_of', [billing_partner_id])]",
        check_company=True,
        tracking=True,
    )
    amount = fields.Monetary(
        currency_field='currency_id',
        required=True,
        tracking=True,
    )
    signed_amount = fields.Monetary(
        string='Signed Amount',
        currency_field='currency_id',
        compute='_compute_signed_amount',
        store=True,
    )
    note = fields.Text()
    delivery_picking_ids = fields.Many2many(
        'stock.picking',
        compute='_compute_delivery_picking_ids',
        string='Deliveries',
    )
    delivery_count = fields.Integer(
        compute='_compute_delivery_picking_ids',
        string='Delivery Count',
    )
    allocation_level = fields.Selection(
        [
            ('order', 'Order'),
            ('line', 'Order Line'),
        ],
        compute='_compute_allocation_level',
        store=True,
    )

    _sql_constraints = [
        (
            'automotive_payment_allocation_amount_positive',
            'CHECK(amount > 0)',
            'Allocated amount must be positive.',
        ),
    ]

    def _is_counted_as_paid(self):
        self.ensure_one()
        return self.active and self.payment_state == 'paid'

    @api.depends('payment_id.name', 'sale_order_id.name', 'sale_order_line_id.sequence')
    def _compute_name(self):
        for allocation in self:
            order_name = allocation.sale_order_id.name or 'Order'
            payment_name = allocation.payment_id.name or allocation.payment_id.display_name or 'Payment'
            if allocation.sale_order_line_id:
                allocation.name = f'{payment_name} / {order_name} / L{allocation.sale_order_line_id.sequence or allocation.sale_order_line_id.id}'
            else:
                allocation.name = f'{payment_name} / {order_name}'

    @api.depends('sale_order_id', 'sale_order_id.partner_id', 'sale_order_id.mechanic_partner_id')
    def _compute_partner_id(self):
        for allocation in self:
            allocation.partner_id = allocation.sale_order_id.mechanic_partner_id or allocation.sale_order_id.partner_id

    @api.depends('sale_order_id', 'sale_order_line_id')
    def _compute_allocation_level(self):
        for allocation in self:
            allocation.allocation_level = 'line' if allocation.sale_order_line_id else 'order'

    @api.depends('payment_type', 'amount')
    def _compute_signed_amount(self):
        for allocation in self:
            sign = -1.0 if allocation.payment_type == 'outbound' else 1.0
            allocation.signed_amount = allocation.amount * sign

    @api.depends('sale_order_id.picking_ids', 'sale_order_id.picking_ids.state', 'sale_order_id.picking_ids.picking_type_code')
    def _compute_delivery_picking_ids(self):
        for allocation in self:
            deliveries = allocation.sale_order_id.picking_ids.filtered(
                lambda picking: picking.picking_type_code == 'outgoing'
            )
            allocation.delivery_picking_ids = deliveries
            allocation.delivery_count = len(deliveries)

    @api.constrains('sale_order_line_id', 'sale_order_id', 'company_id', 'partner_id', 'account_move_id', 'payment_id', 'amount')
    def _check_allocation_consistency(self):
        for allocation in self:
            if allocation.sale_order_line_id and allocation.sale_order_line_id.order_id != allocation.sale_order_id:
                raise ValidationError('The selected order line must belong to the selected order.')

            if allocation.sale_order_id.company_id != allocation.company_id or allocation.payment_id.company_id != allocation.company_id:
                raise ValidationError('Payment allocation company must match the payment and order company.')

            if allocation.payment_id.partner_type != 'customer':
                raise ValidationError('Automotive allocations are only supported for customer payments.')

            if allocation.sale_order_id.currency_id != allocation.payment_id.currency_id:
                raise ValidationError('Payment and order currency must match for automotive allocations.')

            expected_partner = allocation.sale_order_id.mechanic_partner_id or allocation.sale_order_id.partner_id
            if allocation.partner_id and allocation.partner_id.commercial_partner_id != expected_partner.commercial_partner_id:
                raise ValidationError('The allocation customer/mechanic must match the selected order.')

            payment_partner = allocation.payment_id.partner_id.commercial_partner_id
            order_partner = allocation.sale_order_id.partner_id.commercial_partner_id
            if payment_partner and order_partner and payment_partner != order_partner:
                raise ValidationError('The payment billing partner must match the order billing customer.')

            if allocation.account_move_id:
                if allocation.account_move_id.company_id != allocation.company_id:
                    raise ValidationError('The selected invoice/refund must belong to the same company.')
                if allocation.account_move_id.currency_id != allocation.payment_id.currency_id:
                    raise ValidationError('Invoice/refund currency must match the payment currency.')
                if allocation.account_move_id.partner_id.commercial_partner_id != payment_partner:
                    raise ValidationError('The selected invoice/refund must belong to the same billing partner as the payment.')
                if not allocation._invoice_matches_sale_order(allocation.account_move_id, allocation.sale_order_id):
                    raise ValidationError('The selected invoice/refund must be linked to the selected order.')

            total_allocated = sum(
                allocation.payment_id.automotive_allocation_ids.filtered(lambda item: item.active and item.id != allocation.id).mapped('amount')
            ) + allocation.amount
            if float_compare(
                total_allocated,
                allocation.payment_id.amount,
                precision_rounding=allocation.currency_id.rounding if allocation.currency_id else 0.01,
            ) > 0:
                raise ValidationError('The total allocated amount cannot exceed the payment amount.')

            order_net_allocated = sum(
                allocation.sale_order_id.automotive_payment_allocation_ids.filtered(
                    lambda item: item.active and item.id != allocation.id
                ).mapped('signed_amount')
            ) + allocation.signed_amount
            if float_compare(
                order_net_allocated,
                0.0,
                precision_rounding=allocation.sale_order_id.currency_id.rounding,
            ) < 0:
                raise ValidationError('The net allocated amount for an order cannot be negative.')
            if float_compare(
                order_net_allocated,
                allocation.sale_order_id.amount_total,
                precision_rounding=allocation.sale_order_id.currency_id.rounding,
            ) > 0:
                raise ValidationError('The net allocated amount cannot exceed the order total.')

            if allocation.sale_order_line_id:
                line_net_allocated = sum(
                    allocation.sale_order_line_id.automotive_payment_allocation_ids.filtered(
                        lambda item: item.active and item.id != allocation.id
                    ).mapped('signed_amount')
                ) + allocation.signed_amount
                if float_compare(
                    line_net_allocated,
                    0.0,
                    precision_rounding=allocation.sale_order_line_id.currency_id.rounding,
                ) < 0:
                    raise ValidationError('The net allocated amount for an order line cannot be negative.')
                if float_compare(
                    line_net_allocated,
                    allocation.sale_order_line_id.price_total,
                    precision_rounding=allocation.sale_order_line_id.currency_id.rounding,
                ) > 0:
                    raise ValidationError('The net allocated amount cannot exceed the selected order line total.')

    def _invoice_matches_sale_order(self, invoice, sale_order):
        self.ensure_one()
        if not invoice or not sale_order:
            return False

        related_orders = invoice.invoice_line_ids.mapped('sale_line_ids.order_id')
        if related_orders:
            return sale_order in related_orders

        order_invoices = sale_order.invoice_ids
        if order_invoices and invoice in order_invoices:
            return True

        invoice_origin = (invoice.invoice_origin or '').strip()
        if invoice_origin:
            origins = {item.strip() for item in invoice_origin.split(',') if item.strip()}
            return sale_order.name in origins

        return False

    def _audit_payload(self):
        self.ensure_one()
        return {
            'payment_id': self.payment_id.id,
            'sale_order_id': self.sale_order_id.id,
            'sale_order_line_id': self.sale_order_line_id.id if self.sale_order_line_id else False,
            'account_move_id': self.account_move_id.id if self.account_move_id else False,
            'partner_id': self.partner_id.id if self.partner_id else False,
            'amount': self.amount,
            'signed_amount': self.signed_amount,
            'payment_state': self.payment_state,
            'payment_type': self.payment_type,
        }

    def _audit_log(self, action, description, old_values=None, new_values=None):
        self.ensure_one()
        self.env['automotive.audit.log'].log_change(
            action=action,
            record=self,
            description=description,
            old_values=old_values,
            new_values=new_values,
        )

    @api.model_create_multi
    def create(self, vals_list):
        allocations = super().create(vals_list)
        for allocation in allocations:
            allocation._audit_log(
                action='create',
                description=f'Automotive payment allocation created: {allocation.name}',
                new_values=allocation._audit_payload(),
            )
        return allocations

    def write(self, vals):
        old_by_id = {allocation.id: allocation._audit_payload() for allocation in self}
        result = super().write(vals)
        for allocation in self:
            allocation._audit_log(
                action='write',
                description=f'Automotive payment allocation updated: {allocation.name}',
                old_values=old_by_id.get(allocation.id),
                new_values=allocation._audit_payload(),
            )
        return result

    def unlink(self):
        snapshots = {allocation.id: allocation._audit_payload() for allocation in self}
        for allocation in self:
            allocation._audit_log(
                action='unlink',
                description=f'Automotive payment allocation deleted: {allocation.name}',
                old_values=snapshots.get(allocation.id),
            )
        return super().unlink()

    def action_open_deliveries(self):
        self.ensure_one()
        return {
            'name': 'Deliveries',
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.delivery_picking_ids.ids)],
        }


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    _AUDIT_FIELDS = {
        'name',
        'date',
        'amount',
        'state',
        'payment_type',
        'partner_type',
        'partner_id',
        'journal_id',
        'payment_method_line_id',
        'currency_id',
        'partner_bank_id',
        'memo',
        'move_id',
    }

    automotive_allocation_ids = fields.One2many(
        'automotive.payment.allocation',
        'payment_id',
        string='Automotive Allocations',
    )
    automotive_allocated_amount = fields.Monetary(
        string='Allocated Amount',
        currency_field='currency_id',
        compute='_compute_automotive_allocation_amounts',
    )
    automotive_unallocated_amount = fields.Monetary(
        string='Unallocated Amount',
        currency_field='currency_id',
        compute='_compute_automotive_allocation_amounts',
    )
    automotive_order_count = fields.Integer(
        string='Automotive Orders',
        compute='_compute_automotive_allocation_amounts',
    )

    @api.depends('automotive_allocation_ids.amount', 'automotive_allocation_ids.active', 'automotive_allocation_ids.sale_order_id')
    def _compute_automotive_allocation_amounts(self):
        for payment in self:
            active_allocations = payment.automotive_allocation_ids.filtered('active')
            payment.automotive_allocated_amount = sum(active_allocations.mapped('amount'))
            payment.automotive_unallocated_amount = payment.amount - payment.automotive_allocated_amount
            payment.automotive_order_count = len(active_allocations.mapped('sale_order_id'))

    def _audit_snapshot(self, field_names=None):
        self.ensure_one()
        tracked_fields = field_names or self._AUDIT_FIELDS
        snapshot = {
            'automotive_allocated_amount': self.automotive_allocated_amount,
            'automotive_unallocated_amount': self.automotive_unallocated_amount,
            'automotive_order_count': self.automotive_order_count,
            'reconciled_invoice_ids': self.reconciled_invoice_ids.ids,
        }
        for field_name in tracked_fields:
            if field_name not in self._fields:
                continue
            value = self[field_name]
            if isinstance(value, models.BaseModel):
                snapshot[field_name] = value.ids
            else:
                snapshot[field_name] = value
        return snapshot

    def _audit_log(self, action, description, old_values=None, new_values=None):
        self.ensure_one()
        if self.env.context.get('skip_audit_log') is True:
            return False
        return self.env['automotive.audit.log'].log_change(
            action=action,
            record=self,
            description=description,
            old_values=old_values,
            new_values=new_values,
        )

    @api.model_create_multi
    def create(self, vals_list):
        payments = super().create(vals_list)
        if self.env.context.get('skip_audit_log') is not True:
            for payment, vals in zip(payments, vals_list):
                tracked_fields = [field_name for field_name in vals.keys() if field_name in payment._AUDIT_FIELDS] or None
                payment._audit_log(
                    action='create',
                    description=f'Account payment created: {payment.display_name}',
                    new_values=payment._audit_snapshot(tracked_fields),
                )
        return payments

    def write(self, vals):
        context = dict(self.env.context or {})
        tracked_fields = [field_name for field_name in vals.keys() if field_name in self._AUDIT_FIELDS]
        old_by_id = {}
        if tracked_fields and context.get('skip_audit_log') is not True and context.get('skip_payment_lifecycle_audit') is not True:
            old_by_id = {payment.id: payment._audit_snapshot(tracked_fields) for payment in self}

        result = super().write(vals)

        if tracked_fields and context.get('skip_audit_log') is not True and context.get('skip_payment_lifecycle_audit') is not True:
            for payment in self:
                payment._audit_log(
                    action='write',
                    description=f'Account payment updated: {payment.display_name}',
                    old_values=old_by_id.get(payment.id),
                    new_values=payment._audit_snapshot(tracked_fields),
                )
        return result

    def unlink(self):
        context = dict(self.env.context or {})
        snapshots = {payment.id: payment._audit_snapshot() for payment in self}
        if context.get('skip_audit_log') is not True:
            for payment in self:
                payment._audit_log(
                    action='unlink',
                    description=f'Account payment deleted: {payment.display_name}',
                    old_values=snapshots.get(payment.id),
                )
        return super().unlink()

    def action_view_automotive_allocations(self):
        self.ensure_one()
        return {
            'name': 'Automotive Payment Allocations',
            'type': 'ir.actions.act_window',
            'res_model': 'automotive.payment.allocation',
            'view_mode': 'list,form',
            'domain': [('payment_id', '=', self.id)],
            'context': {
                'default_payment_id': self.id,
                'default_company_id': self.company_id.id,
            },
        }

    def action_generate_automotive_allocations(self):
        SaleOrder = self.env['sale.order']
        for payment in self:
            if payment.partner_type != 'customer':
                raise UserError('Automotive allocations can only be generated for customer payments.')
            remaining = payment.automotive_unallocated_amount
            if float_compare(remaining, 0.0, precision_rounding=payment.currency_id.rounding) <= 0:
                continue

            invoices = payment.reconciled_invoice_ids.filtered(
                lambda move: (
                    move.move_type in {'out_invoice', 'out_receipt'} if payment.payment_type == 'inbound'
                    else move.move_type == 'out_refund'
                ) and move.currency_id == payment.currency_id
            )
            if not invoices:
                raise UserError('No customer invoices are linked to this payment yet.')

            for invoice in invoices:
                if float_compare(remaining, 0.0, precision_rounding=payment.currency_id.rounding) <= 0:
                    break
                order_amounts = {}
                for line in invoice.invoice_line_ids:
                    sale_lines = line.sale_line_ids.filtered(lambda sale_line: sale_line.order_id)
                    linked_orders = sale_lines.mapped('order_id')
                    if not linked_orders:
                        continue
                    line_amount = abs(line.price_total)
                    total_linked_amount = sum(abs(sale_line.price_total) for sale_line in sale_lines)
                    if float_compare(total_linked_amount, 0.0, precision_rounding=payment.currency_id.rounding) <= 0:
                        share_per_order = line_amount / len(linked_orders)
                        for order in linked_orders:
                            order_amounts[order] = order_amounts.get(order, 0.0) + share_per_order
                        continue
                    for order in linked_orders:
                        order_sale_lines = sale_lines.filtered(lambda sale_line: sale_line.order_id == order)
                        order_basis = sum(abs(sale_line.price_total) for sale_line in order_sale_lines)
                        if float_compare(order_basis, 0.0, precision_rounding=payment.currency_id.rounding) <= 0:
                            continue
                        share = line_amount * (order_basis / total_linked_amount)
                        order_amounts[order] = order_amounts.get(order, 0.0) + share
                if not order_amounts and invoice.invoice_origin:
                    order = SaleOrder.search([('name', '=', invoice.invoice_origin)], limit=1)
                    if order:
                        order_amounts[order] = abs(invoice.amount_total)

                for order, order_amount in order_amounts.items():
                    if float_compare(remaining, 0.0, precision_rounding=payment.currency_id.rounding) <= 0:
                        break
                    existing = sum(
                        payment.automotive_allocation_ids.filtered(
                            lambda allocation: allocation.active and allocation.sale_order_id == order and allocation.account_move_id == invoice
                        ).mapped('amount')
                    )
                    allocatable = max(order_amount - existing, 0.0)
                    if float_compare(allocatable, 0.0, precision_rounding=payment.currency_id.rounding) <= 0:
                        continue
                    amount = min(remaining, allocatable)
                    self.env['automotive.payment.allocation'].create({
                        'company_id': payment.company_id.id,
                        'payment_id': payment.id,
                        'sale_order_id': order.id,
                        'account_move_id': invoice.id,
                        'amount': amount,
                    })
                    remaining -= amount
            payment.env['automotive.audit.log'].log_change(
                action='custom',
                record=payment,
                description='Generated automotive payment allocations from linked invoices.',
                new_values={
                    'automotive_allocated_amount': payment.automotive_allocated_amount,
                    'automotive_unallocated_amount': payment.automotive_unallocated_amount,
                    'automotive_order_count': payment.automotive_order_count,
                },
            )
        return True

    def action_post(self):
        for payment in self:
            payment.automotive_allocation_ids._check_allocation_consistency()
        old_by_id = {payment.id: payment._audit_snapshot() for payment in self}
        result = super(AccountPayment, self.with_context(skip_payment_lifecycle_audit=True)).action_post()
        if self.env.context.get('skip_audit_log') is not True:
            for payment in self:
                payment._audit_log(
                    action='custom',
                    description=f'Account payment posted: {payment.display_name}',
                    old_values=old_by_id.get(payment.id),
                    new_values=payment._audit_snapshot(),
                )
        return result

    def action_cancel(self):
        old_by_id = {payment.id: payment._audit_snapshot() for payment in self}
        result = super(AccountPayment, self.with_context(skip_payment_lifecycle_audit=True)).action_cancel()
        if self.env.context.get('skip_audit_log') is not True:
            for payment in self:
                payment._audit_log(
                    action='custom',
                    description=f'Account payment cancelled: {payment.display_name}',
                    old_values=old_by_id.get(payment.id),
                    new_values=payment._audit_snapshot(),
                )
        return result

    def action_draft(self):
        old_by_id = {payment.id: payment._audit_snapshot() for payment in self}
        result = super(AccountPayment, self.with_context(skip_payment_lifecycle_audit=True)).action_draft()
        if self.env.context.get('skip_audit_log') is not True:
            for payment in self:
                payment._audit_log(
                    action='custom',
                    description=f'Account payment reset to draft: {payment.display_name}',
                    old_values=old_by_id.get(payment.id),
                    new_values=payment._audit_snapshot(),
                )
        return result


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    automotive_payment_allocation_ids = fields.One2many(
        'automotive.payment.allocation',
        'sale_order_id',
        string='Payment Allocations',
    )
    automotive_paid_amount = fields.Monetary(
        string='Paid Amount',
        currency_field='currency_id',
        compute='_compute_automotive_payment_summary',
    )
    automotive_amount_due = fields.Monetary(
        string='Amount Due',
        currency_field='currency_id',
        compute='_compute_automotive_payment_summary',
    )
    automotive_payment_status = fields.Selection(
        [
            ('unpaid', 'Unpaid'),
            ('partial', 'Partial'),
            ('paid', 'Paid'),
            ('overpaid', 'Overpaid'),
        ],
        string='Automotive Payment Status',
        compute='_compute_automotive_payment_summary',
    )
    automotive_payment_count = fields.Integer(
        string='Payment Allocations',
        compute='_compute_automotive_payment_summary',
    )

    @api.depends(
        'automotive_payment_allocation_ids.signed_amount',
        'automotive_payment_allocation_ids.active',
        'automotive_payment_allocation_ids.payment_state',
        'amount_total',
    )
    def _compute_automotive_payment_summary(self):
        for order in self:
            allocations = order.automotive_payment_allocation_ids.filtered(
                lambda allocation: allocation._is_counted_as_paid()
            )
            paid_amount = sum(allocations.mapped('signed_amount'))
            order.automotive_paid_amount = paid_amount
            order.automotive_amount_due = order.amount_total - paid_amount
            order.automotive_payment_count = len(allocations.mapped('payment_id'))
            if float_compare(paid_amount, order.amount_total, precision_rounding=order.currency_id.rounding) > 0:
                order.automotive_payment_status = 'overpaid'
            elif float_compare(paid_amount, order.amount_total, precision_rounding=order.currency_id.rounding) == 0 and paid_amount:
                order.automotive_payment_status = 'paid'
            elif float_compare(paid_amount, 0.0, precision_rounding=order.currency_id.rounding) > 0:
                order.automotive_payment_status = 'partial'
            else:
                order.automotive_payment_status = 'unpaid'

    def action_view_automotive_payment_allocations(self):
        self.ensure_one()
        return {
            'name': 'Payment Allocations',
            'type': 'ir.actions.act_window',
            'res_model': 'automotive.payment.allocation',
            'view_mode': 'list,form',
            'domain': [('sale_order_id', '=', self.id)],
            'context': {
                'default_sale_order_id': self.id,
                'default_company_id': self.company_id.id,
            },
        }


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    automotive_payment_allocation_ids = fields.One2many(
        'automotive.payment.allocation',
        'sale_order_line_id',
        string='Line Payment Allocations',
    )
    automotive_paid_amount = fields.Monetary(
        string='Paid Amount',
        currency_field='currency_id',
        compute='_compute_automotive_payment_amounts',
    )
    automotive_amount_due = fields.Monetary(
        string='Amount Due',
        currency_field='currency_id',
        compute='_compute_automotive_payment_amounts',
    )

    @api.depends(
        'automotive_payment_allocation_ids.signed_amount',
        'automotive_payment_allocation_ids.active',
        'automotive_payment_allocation_ids.payment_state',
        'price_total',
    )
    def _compute_automotive_payment_amounts(self):
        for line in self:
            allocations = line.automotive_payment_allocation_ids.filtered(
                lambda allocation: allocation._is_counted_as_paid()
            )
            paid_amount = sum(allocations.mapped('signed_amount'))
            line.automotive_paid_amount = paid_amount
            line.automotive_amount_due = line.price_total - paid_amount
