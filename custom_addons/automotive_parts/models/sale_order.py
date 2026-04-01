# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare


class SaleOrder(models.Model):
    """Extended Sale Order with automotive workflow"""
    _inherit = 'sale.order'

    # Custom order states
    auto_state = fields.Selection([
        ('draft', 'Draft'),
        ('waiting_supply', 'În așteptare aprovizionare'),
        ('partial_received', 'Parțial recepționată'),
        ('fully_received', 'Complet recepționată'),
        ('ready_prep', 'Gata de pregătire'),
        ('delivered', 'Livrată'),
        ('cancel', 'Anulată'),
    ], string='Stare Comandă Auto', default='draft', tracking=True)

    # Order type
    order_type = fields.Selection([
        ('internal', 'Comandă Internă'),
        ('external', 'Comandă Externă'),
    ], string='Tip Comandă', default='external')
    mechanic_partner_id = fields.Many2one(
        'res.partner',
        string='Mecanic',
        domain="[('client_type', '=', 'mechanic'), ('active', '=', True)]",
        index=True,
        help='Mechanic who should see this quotation/order in the dedicated portal.',
    )

    # Delivery information
    estimated_delivery_date = fields.Date('Dată Livrare Estimată')
    responsible_user_id = fields.Many2one('res.users', 'Responsabil Intern',
                                           default=lambda self: self.env.user)

    # Stock status
    stock_status = fields.Selection([
        ('none', 'Fără Stoc'),
        ('partial', 'Parțial în Stoc'),
        ('full', 'Complet în Stoc'),
    ], string='Status Stoc', compute='_compute_stock_status', store=True)

    observations = fields.Text('Observații')
    automotive_inbound_paid_amount = fields.Monetary(
        string='Plăți încasate',
        currency_field='currency_id',
        compute='_compute_automotive_financial_truth',
    )
    automotive_refund_amount = fields.Monetary(
        string='Retururi / refund-uri',
        currency_field='currency_id',
        compute='_compute_automotive_financial_truth',
    )
    automotive_return_amount = fields.Monetary(
        string='Retururi operaționale',
        currency_field='currency_id',
        compute='_compute_automotive_financial_truth',
    )
    automotive_credit_adjustment_total = fields.Monetary(
        string='Ajustări credit folosite în sold',
        currency_field='currency_id',
        compute='_compute_automotive_financial_truth',
    )
    automotive_financial_balance_due = fields.Monetary(
        string='Sold operațional ajustat',
        currency_field='currency_id',
        compute='_compute_automotive_financial_truth',
    )
    automotive_balance_formula = fields.Char(
        string='Formula sold',
        compute='_compute_automotive_financial_truth',
    )
    _READY_ACTIVITY_SUMMARY = 'Comanda gata de pregătire'

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

    @api.depends(
        'state',
        'order_line',
        'order_line.product_id',
        'order_line.product_uom_qty',
        'order_line.product_uom',
        'order_line.qty_reserved',
        'order_line.qty_received',
    )
    def _compute_stock_status(self):
        """Compute stock availability status"""
        for order in self:
            if not order.order_line:
                order.stock_status = 'none'
                continue

            relevant_lines = order.order_line.filtered(lambda l: l.product_id and l.product_id.is_storable)
            if not relevant_lines:
                order.stock_status = 'full'
                continue

            lines_full = 0
            lines_partial = 0
            total_lines = len(relevant_lines)

            for line in relevant_lines:
                ready_qty = line._get_ready_qty()
                needed = line.product_uom_qty
                rounding = line.product_uom.rounding
                if float_compare(ready_qty, needed, precision_rounding=rounding) >= 0:
                    lines_full += 1
                elif float_compare(ready_qty, 0.0, precision_rounding=rounding) > 0:
                    lines_partial += 1

            if lines_full == 0 and lines_partial == 0:
                order.stock_status = 'none'
            elif lines_full == total_lines:
                order.stock_status = 'full'
            else:
                order.stock_status = 'partial'

    @api.model_create_multi
    def create(self, vals_list):
        """Override create to reserve stock"""
        partner_model = self.env['res.partner']
        for vals in vals_list:
            if vals.get('mechanic_partner_id') or not vals.get('partner_id'):
                continue
            partner = partner_model.browse(vals['partner_id'])
            if partner.client_type == 'mechanic':
                vals['mechanic_partner_id'] = partner.commercial_partner_id.id

        orders = super().create(vals_list)

        for order in orders:
            order._sync_mechanic_followers()
            # Reserve stock automatically
            if order.state in ['sale', 'done']:
                order._reserve_stock()

        audit_log = self.env['automotive.audit.log']
        for order, vals in zip(orders, vals_list):
            tracked_fields = [f for f in vals.keys() if f in order._fields]
            audit_log.log_change(
                action='create',
                record=order,
                description=f'Created order: {order.name}',
                new_values=order._audit_snapshot(tracked_fields),
            )

        return orders

    def write(self, vals):
        """Override write to enforce rules and update auto_state"""
        context = dict(self.env.context or {})
        if context.get('skip_edit_restriction') is not True:
            self._ensure_order_editable(vals)

        tracked_fields = [f for f in vals.keys() if f in self._fields]
        old_by_id = {}
        if context.get('skip_audit_log') is not True:
            old_by_id = {order.id: order._audit_snapshot(tracked_fields) for order in self}
        old_mechanic_partner_ids = {}
        if 'mechanic_partner_id' in vals:
            old_mechanic_partner_ids = {
                order.id: order._get_mechanic_portal_partner().id for order in self
            }

        result = super().write(vals)
        if 'mechanic_partner_id' in vals:
            self._sync_mechanic_followers(old_mechanic_partner_ids=old_mechanic_partner_ids)

        skip_auto_state_update = context.get('skip_auto_state_update') is True or 'auto_state' in vals
        if not skip_auto_state_update:
            self._update_auto_state()

        if context.get('skip_audit_log') is not True:
            audit_log = self.env['automotive.audit.log']
            for order in self:
                audit_log.log_change(
                    action='write',
                    record=order,
                    description=f'Modified order: {order.name}',
                    old_values=old_by_id.get(order.id),
                    new_values=order._audit_snapshot(tracked_fields),
                )

        return result

    def _ensure_order_editable(self, vals):
        restricted_fields = {
            'order_line',
            'partner_id',
            'order_type',
            'estimated_delivery_date',
            'responsible_user_id',
            'mechanic_partner_id',
            'observations',
            'pricelist_id',
            'payment_term_id',
            'currency_id',
        }
        if not restricted_fields.intersection(vals.keys()):
            return

        for order in self:
            if order.auto_state in {'ready_prep', 'delivered'}:
                raise UserError('Comanda nu mai poate fi modificată după starea „Gata de pregătire”.')

    @api.onchange('partner_id')
    def _onchange_partner_id_set_mechanic(self):
        for order in self:
            if order.partner_id and order.partner_id.client_type == 'mechanic':
                order.mechanic_partner_id = order.partner_id.commercial_partner_id

    def _get_mechanic_portal_partner(self):
        self.ensure_one()
        return self.mechanic_partner_id.commercial_partner_id

    def _sync_mechanic_followers(self, old_mechanic_partner_ids=None):
        old_mechanic_partner_ids = old_mechanic_partner_ids or {}
        for order in self:
            new_partner = order._get_mechanic_portal_partner()
            old_partner_id = old_mechanic_partner_ids.get(order.id)
            if old_partner_id and old_partner_id != new_partner.id:
                order.message_unsubscribe(partner_ids=[old_partner_id])
            if new_partner and new_partner not in order.message_partner_ids:
                order.message_subscribe(partner_ids=[new_partner.id])

    def _post_portal_visible_event(self, body):
        self.ensure_one()
        self.message_post(
            body=body,
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )

    def _get_ready_notification_partner(self):
        self.ensure_one()
        return self.mechanic_partner_id or self.partner_id

    def _get_ready_notification_email(self):
        self.ensure_one()
        partner = self._get_ready_notification_partner()
        return (partner.email or '').strip()

    def _get_ready_notification_name(self):
        self.ensure_one()
        partner = self._get_ready_notification_partner()
        return partner.name or ''

    def _get_returned_amount_total(self):
        self.ensure_one()
        total = 0.0
        relevant_lines = self.order_line.filtered(lambda line: line.product_id and line.product_id.is_storable and not line.display_type)
        for line in relevant_lines:
            delivered_moves = line.move_ids.filtered(
                lambda move: move.state == 'done'
                and not move.scrapped
                and move.location_dest_id.usage == 'customer'
            )
            if not delivered_moves:
                continue

            returned_qty = 0.0
            for delivery_move in delivered_moves:
                for returned_move in delivery_move.returned_move_ids.filtered(
                    lambda move: move.state == 'done' and not move.scrapped
                ):
                    returned_qty += returned_move.product_uom._compute_quantity(
                        returned_move.quantity,
                        line.product_uom,
                        rounding_method='HALF-UP',
                    )

            if float_compare(returned_qty, 0.0, precision_rounding=line.product_uom.rounding) <= 0:
                continue

            ordered_qty = line.product_uom_qty
            if float_compare(ordered_qty, 0.0, precision_rounding=line.product_uom.rounding) <= 0:
                continue

            effective_return_qty = min(returned_qty, ordered_qty)
            total += abs(line.price_total) * (effective_return_qty / ordered_qty)

        return total

    @api.depends(
        'amount_total',
        'invoice_ids.state',
        'invoice_ids.move_type',
        'invoice_ids.amount_total',
        'order_line.price_total',
        'order_line.product_id',
        'order_line.product_uom_qty',
        'order_line.product_uom',
        'order_line.display_type',
        'order_line.move_ids.state',
        'order_line.move_ids.scrapped',
        'order_line.move_ids.location_dest_id.usage',
        'order_line.move_ids.quantity',
        'order_line.move_ids.product_uom',
        'order_line.move_ids.returned_move_ids.state',
        'order_line.move_ids.returned_move_ids.scrapped',
        'order_line.move_ids.returned_move_ids.quantity',
        'order_line.move_ids.returned_move_ids.product_uom',
        'automotive_payment_allocation_ids.amount',
        'automotive_payment_allocation_ids.payment_type',
        'automotive_payment_allocation_ids.active',
        'automotive_payment_allocation_ids.payment_state',
    )
    def _compute_automotive_financial_truth(self):
        for order in self:
            active_allocations = order.automotive_payment_allocation_ids.filtered(
                lambda allocation: allocation.active and allocation.payment_state == 'paid'
            )
            inbound_paid_amount = sum(
                active_allocations.filtered(lambda allocation: allocation.payment_type == 'inbound').mapped('amount')
            )
            outbound_refund_amount = sum(
                active_allocations.filtered(lambda allocation: allocation.payment_type == 'outbound').mapped('amount')
            )
            invoice_refund_amount = sum(
                abs(move.amount_total)
                for move in order.invoice_ids.filtered(
                    lambda move: move.state == 'posted' and move.move_type == 'out_refund'
                )
            )
            refund_amount = max(outbound_refund_amount, invoice_refund_amount)
            return_amount = order._get_returned_amount_total()
            credit_adjustment_total = max(refund_amount, return_amount)

            order.automotive_inbound_paid_amount = inbound_paid_amount
            order.automotive_refund_amount = refund_amount
            order.automotive_return_amount = return_amount
            order.automotive_credit_adjustment_total = credit_adjustment_total
            order.automotive_financial_balance_due = order.amount_total - inbound_paid_amount - credit_adjustment_total
            order.automotive_balance_formula = (
                'Sold operațional = Total comenzi - Plăți încasate - Ajustări retur/refund '
                '(ajustarea folosește valoarea confirmată cea mai mare dintre retururile operaționale și documentele de credit)'
            )

    def _reserve_stock(self):
        """Reserve stock for order lines"""
        for order in self:
            for line in order.order_line:
                if line.product_id.is_storable:
                    # Check if enough stock
                    available = line.product_id.stock_available

                    if available < line.product_uom_qty:
                        order._post_portal_visible_event(
                            body=(
                                f'Stoc insuficient pentru {line.product_id.name}. '
                                f'Disponibil: {available}, necesar: {line.product_uom_qty}.'
                            )
                        )

    def action_confirm(self):
        result = super().action_confirm()
        commercial_archive = self.env['commercial.document.archive']
        for order in self:
            commercial_archive.sync_from_source_document(
                order,
                document_type='internal',
                note=f'Automatically linked from order confirmation {order.name}.',
                archive=True,
            )
            order._sync_mechanic_followers()
            order._update_auto_state()
        return result

    def _update_auto_state(self):
        """Update automatic state based on stock availability"""
        for order in self:
            previous_state = order.auto_state
            if order.state == 'cancel':
                desired = 'cancel'
            elif order._is_fully_delivered():
                desired = 'delivered'
            elif order.state == 'draft':
                desired = 'draft'
            else:
                relevant_lines = order.order_line.filtered(lambda l: l.product_id and l.product_id.is_storable)
                if not relevant_lines:
                    desired = 'ready_prep'
                else:
                    all_reserved = True
                    all_received = True
                    any_progress = False
                    for line in relevant_lines:
                        needed = line.product_uom_qty
                        rounding = line.product_uom.rounding
                        reserved_ok = float_compare(line.qty_reserved, needed, precision_rounding=rounding) >= 0
                        received_ok = float_compare(line.qty_received, needed, precision_rounding=rounding) >= 0
                        has_progress = (
                            float_compare(line.qty_reserved, 0.0, precision_rounding=rounding) > 0
                            or float_compare(line.qty_received, 0.0, precision_rounding=rounding) > 0
                        )
                        all_reserved = all_reserved and reserved_ok
                        all_received = all_received and received_ok
                        any_progress = any_progress or has_progress

                    if all_received and previous_state not in {'ready_prep', 'delivered'}:
                        desired = 'fully_received'
                    elif all_reserved:
                        desired = 'ready_prep'
                    elif any_progress:
                        desired = 'partial_received'
                    else:
                        desired = 'waiting_supply'

            if previous_state == desired:
                continue

            order.with_context(skip_auto_state_update=True, skip_audit_log=True).write({'auto_state': desired})
            order._log_auto_state_transition(previous_state, desired, origin='automatic')

    def _log_auto_state_transition(self, previous_state, new_state, origin='automatic'):
        self.ensure_one()
        if previous_state == new_state:
            return
        state_labels = dict(self._fields['auto_state'].selection)
        previous_label = state_labels.get(previous_state, previous_state)
        new_label = state_labels.get(new_state, new_state)
        mode_label = 'automat' if origin == 'automatic' else 'manual'

        self._post_portal_visible_event(
            body=f'Starea comenzii a fost actualizata {mode_label}: {previous_label} -> {new_label}.',
        )
        self.env['automotive.audit.log'].log_change(
            action='custom',
            record=self,
            description=f'Order auto state changed from {previous_label} to {new_label}',
            old_values={'auto_state': previous_state},
            new_values={
                'auto_state': new_state,
                'stock_status': self.stock_status,
                'transition_origin': origin,
            },
        )

        if new_state == 'ready_prep':
            self._schedule_ready_activity()
            self._send_ready_email_notification()
        else:
            self._clear_ready_activity()

    def _schedule_ready_activity(self):
        self.ensure_one()
        self._clear_ready_activity()
        user = self.responsible_user_id or self.user_id or self.env.user
        if not user:
            return

        self.activity_schedule(
            act_type_xmlid='mail.mail_activity_data_todo',
            user_id=user.id,
            summary=self._READY_ACTIVITY_SUMMARY,
            note='Comanda este gata de pregătire/livrare.',
            date_deadline=fields.Date.context_today(self),
        )

    def _clear_ready_activity(self):
        self.ensure_one()
        todo_activity = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        if not todo_activity:
            return
        ready_activities = self.activity_ids.filtered(
            lambda activity: activity.activity_type_id == todo_activity
            and activity.summary == self._READY_ACTIVITY_SUMMARY
        )
        ready_activities.unlink()

    def _send_ready_email_notification(self):
        self.ensure_one()
        company = self.company_id or self.env.company
        if not company.automotive_ready_email_enabled:
            return
        recipient_email = self._get_ready_notification_email()
        if not recipient_email:
            return

        template = (
            company.automotive_ready_email_template_id
            or self.env.ref('automotive_parts.mail_template_order_ready', raise_if_not_found=False)
        )
        if not template:
            return

        self.with_context(
            force_send=True,
            automotive_ready_email_to=recipient_email,
            automotive_ready_recipient_name=self._get_ready_notification_name(),
        ).message_post_with_source(
            template,
            email_layout_xmlid='mail.mail_notification_layout_with_responsible_signature',
            subtype_xmlid='mail.mt_comment',
        )

    def _is_fully_delivered(self):
        self.ensure_one()
        relevant_lines = self.order_line.filtered(
            lambda l: l.product_id and l.product_id.is_storable and not l.display_type
        )
        if not relevant_lines:
            return False

        for line in relevant_lines:
            delivered = line.qty_delivered
            needed = line.product_uom_qty
            rounding = line.product_uom.rounding
            if float_compare(delivered, needed, precision_rounding=rounding) < 0:
                return False
        return True

    def _refresh_automotive_stock_state(self):
        lines = self.mapped('order_line').filtered(lambda line: line.product_id and line.product_id.is_storable)
        if lines:
            lines._compute_qty_reserved()
            lines._compute_qty_received()
            lines._compute_line_state()
        self._compute_stock_status()
        self._update_auto_state()

    def _get_portal_mechanic_status(self):
        """Return portal-ready automotive status metadata for website pages."""
        self.ensure_one()

        auto_state_labels = dict(self._fields['auto_state'].selection)
        stock_status_labels = dict(self._fields['stock_status'].selection)
        auto_state_classes = {
            'draft': 'secondary',
            'waiting_supply': 'warning',
            'partial_received': 'warning',
            'fully_received': 'info',
            'ready_prep': 'primary',
            'delivered': 'success',
            'cancel': 'danger',
        }
        stock_status_classes = {
            'none': 'danger',
            'partial': 'warning',
            'full': 'success',
        }

        outgoing_pickings = self.picking_ids.filtered(
            lambda picking: picking.picking_type_id.code == 'outgoing'
        )
        latest_picking = outgoing_pickings.sorted(
            key=lambda picking: picking.scheduled_date or picking.date_done or picking.create_date or fields.Datetime.from_string('1970-01-01 00:00:00'),
            reverse=True,
        )[:1]

        delivery_label = False
        delivery_class = 'secondary'
        delivery_date = False
        if latest_picking:
            picking = latest_picking[0]
            delivery_date = picking.scheduled_date or picking.date_done
            if picking.state == 'done':
                delivery_label = 'Livrat'
                delivery_class = 'success'
            elif picking.state in ('assigned', 'confirmed', 'waiting'):
                delivery_label = 'În magazin / în pregătire'
                delivery_class = 'info'
            elif picking.state == 'cancel':
                delivery_label = 'Anulată'
                delivery_class = 'danger'
            else:
                delivery_label = 'În așteptare'
                delivery_class = 'warning'

        return {
            'auto_state_label': auto_state_labels.get(self.auto_state, self.auto_state),
            'auto_state_class': auto_state_classes.get(self.auto_state, 'secondary'),
            'stock_status_label': stock_status_labels.get(self.stock_status, self.stock_status),
            'stock_status_class': stock_status_classes.get(self.stock_status, 'secondary'),
            'delivery_label': delivery_label,
            'delivery_class': delivery_class,
            'delivery_date': delivery_date,
            'latest_picking': latest_picking[0] if latest_picking else False,
        }

    def action_mark_delivered(self):
        """Mark order as delivered"""
        self.ensure_one()
        outgoing_pickings = self.picking_ids.filtered(
            lambda picking: picking.picking_type_code == 'outgoing' and picking.state == 'done'
        )
        if not outgoing_pickings or not self._is_fully_delivered():
            raise UserError('Order can only be marked as delivered after the related outgoing transfer is completed.')
        previous_state = self.auto_state
        self.with_context(skip_auto_state_update=True).write({'auto_state': 'delivered'})
        self._log_auto_state_transition(previous_state, 'delivered', origin='manual')

    def action_cancel_order(self):
        """Cancel order and release stock"""
        for order in self:
            previous_state = order.auto_state
            order.with_context(skip_auto_state_update=True, skip_edit_restriction=True).write({'auto_state': 'cancel'})
            order.with_context(skip_edit_restriction=True).action_cancel()
            order._log_auto_state_transition(previous_state, 'cancel', origin='manual')


class SaleOrderLine(models.Model):
    """Extended Sale Order Line"""
    _inherit = 'sale.order.line'

    _AUDIT_FIELDS = {
        'order_id',
        'product_id',
        'name',
        'product_uom_qty',
        'product_uom',
        'price_unit',
        'discount',
        'tax_id',
        'display_type',
    }

    # Line-specific stock info
    qty_reserved = fields.Float('Cantitate Rezervată', compute='_compute_qty_reserved', store=True)
    qty_received = fields.Float('Cantitate Recepționată', compute='_compute_qty_received', store=True)

    line_state = fields.Selection([
        ('incomplete', 'Necompletată'),
        ('complete', 'Completată'),
    ], string='Stare Poziție', default='incomplete', compute='_compute_line_state', store=True)

    def _audit_snapshot(self, field_names=None):
        self.ensure_one()
        tracked_fields = field_names or self._AUDIT_FIELDS
        snapshot = {
            'currency_id': self.currency_id.id if self.currency_id else False,
            'qty_reserved': self.qty_reserved,
            'qty_received': self.qty_received,
            'line_state': self.line_state,
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
        lines = super().create(vals_list)
        if self.env.context.get('skip_audit_log') is not True:
            for line, vals in zip(lines, vals_list):
                tracked_fields = [field_name for field_name in vals.keys() if field_name in line._AUDIT_FIELDS] or None
                line._audit_log(
                    action='create',
                    description=f'Sale order line created on {line.order_id.name or line.order_id.display_name}: {line.display_name}',
                    new_values=line._audit_snapshot(tracked_fields),
                )
        return lines

    def write(self, vals):
        context = dict(self.env.context or {})
        tracked_fields = [field_name for field_name in vals.keys() if field_name in self._AUDIT_FIELDS]
        old_by_id = {}
        if tracked_fields and context.get('skip_audit_log') is not True:
            old_by_id = {line.id: line._audit_snapshot(tracked_fields) for line in self}

        result = super().write(vals)

        if tracked_fields and context.get('skip_audit_log') is not True:
            for line in self:
                line._audit_log(
                    action='write',
                    description=f'Sale order line modified on {line.order_id.name or line.order_id.display_name}: {line.display_name}',
                    old_values=old_by_id.get(line.id),
                    new_values=line._audit_snapshot(tracked_fields),
                )
        return result

    def unlink(self):
        context = dict(self.env.context or {})
        snapshots = {line.id: line._audit_snapshot() for line in self}
        if context.get('skip_audit_log') is not True:
            for line in self:
                line._audit_log(
                    action='unlink',
                    description=f'Sale order line deleted from {line.order_id.name or line.order_id.display_name}: {line.display_name}',
                    old_values=snapshots.get(line.id),
                )
        return super().unlink()

    @api.depends(
        'state',
        'product_id',
        'product_uom_qty',
        'product_uom',
        'move_ids',
        'move_ids.state',
        'move_ids.scrapped',
        'move_ids.location_dest_id.usage',
        'move_ids.quantity',
        'move_ids.product_uom',
    )
    def _compute_qty_reserved(self):
        """Compute reserved quantity"""
        for line in self:
            if line.state not in {'sale', 'done'} or not line.product_id or not line.product_id.is_storable:
                line.qty_reserved = 0.0
                continue

            qty = 0.0
            moves = line.move_ids.filtered(
                lambda m: m.state not in {'cancel', 'done'}
                and not m.scrapped
                and m.location_dest_id.usage == 'customer'
            )
            for move in moves:
                qty += move.product_uom._compute_quantity(move.quantity, line.product_uom, rounding_method='HALF-UP')
            line.qty_reserved = qty

    @api.depends(
        'state',
        'product_id',
        'product_uom',
        'move_ids',
        'move_ids.state',
        'move_ids.scrapped',
        'move_ids.location_dest_id.usage',
        'move_ids.move_orig_ids.state',
        'move_ids.move_orig_ids.scrapped',
        'move_ids.move_orig_ids.location_dest_id.usage',
        'move_ids.move_orig_ids.move_orig_ids.state',
        'move_ids.move_orig_ids.move_orig_ids.scrapped',
        'move_ids.move_orig_ids.move_orig_ids.location_dest_id.usage',
        'move_ids.move_orig_ids.quantity',
        'move_ids.move_orig_ids.product_uom',
        'move_ids.move_orig_ids.move_orig_ids.quantity',
        'move_ids.move_orig_ids.move_orig_ids.product_uom',
    )
    def _compute_qty_received(self):
        for line in self:
            if line.state not in {'sale', 'done'} or not line.product_id or not line.product_id.is_storable:
                line.qty_received = 0.0
                continue

            delivery_moves = line.move_ids.filtered(
                lambda m: m.state != 'cancel'
                and not m.scrapped
                and m.location_dest_id.usage == 'customer'
            )
            qty = 0.0
            for move in delivery_moves:
                qty += line._get_received_supply_qty_for_delivery_move(move)
            line.qty_received = qty

    @api.depends('qty_received', 'qty_reserved', 'product_uom_qty', 'product_uom')
    def _compute_line_state(self):
        """Compute if line is complete"""
        for line in self:
            ready_qty = line._get_ready_qty()
            if float_compare(ready_qty, line.product_uom_qty, precision_rounding=line.product_uom.rounding) >= 0:
                line.line_state = 'complete'
            else:
                line.line_state = 'incomplete'

    def _get_ready_qty(self):
        self.ensure_one()
        return max(self.qty_reserved, self.qty_received)

    def _get_supply_target_moves(self, receipt_location):
        self.ensure_one()
        if not self.product_id or not self.product_id.is_storable:
            return self.env['stock.move']

        receipt_location = receipt_location if receipt_location and receipt_location.exists() else False
        if not receipt_location:
            return self.env['stock.move']

        delivery_moves = self.move_ids.filtered(
            lambda move: move.state not in {'done', 'cancel'}
            and not move.scrapped
            and move.location_dest_id.usage == 'customer'
        )
        if not delivery_moves:
            return self.env['stock.move']

        candidates = delivery_moves | self._collect_origin_moves(delivery_moves)
        return candidates.filtered(
            lambda move: move.state not in {'done', 'cancel'}
            and not move.scrapped
            and move.product_id == self.product_id
            and move.location_id == receipt_location
        ).sorted(lambda move: (move.priority or '0', move.date or fields.Datetime.now(), move.id))

    def _collect_origin_moves(self, moves):
        """Collect upstream moves recursively, keeping only each move once."""
        all_origins = self.env['stock.move']
        to_visit = moves.mapped('move_orig_ids')
        while to_visit:
            new_moves = to_visit - all_origins
            if not new_moves:
                break
            all_origins |= new_moves
            to_visit = new_moves.mapped('move_orig_ids')
        return all_origins

    def _get_received_supply_qty_for_delivery_move(self, delivery_move):
        self.ensure_one()
        incoming_qty = 0.0
        visited = self.env['stock.move']
        to_visit = delivery_move.move_orig_ids
        while to_visit:
            move = to_visit[:1]
            to_visit -= move
            if move in visited:
                continue
            visited |= move
            if move.scrapped or move.state == 'cancel':
                continue
            if move.state == 'done' and move.location_dest_id.usage == 'internal':
                incoming_qty += move.product_uom._compute_quantity(
                    move.quantity,
                    self.product_uom,
                    rounding_method='HALF-UP',
                )
                continue
            to_visit |= move.move_orig_ids

        delivery_qty = delivery_move.product_uom._compute_quantity(
            delivery_move.product_uom_qty,
            self.product_uom,
            rounding_method='HALF-UP',
        )
        return min(incoming_qty, delivery_qty)
