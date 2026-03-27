# -*- coding: utf-8 -*-
from odoo import http, _, fields
from odoo.http import request

from odoo.addons.sale.controllers.portal import CustomerPortal as SaleCustomerPortal
from odoo.addons.portal.controllers.portal import pager as portal_pager


class CustomerPortal(SaleCustomerPortal):
    """Mechanic-facing portal extensions built on top of standard sale portal."""

    def _is_mechanic_portal_user(self):
        return request.env.user.has_group('automotive_parts.group_mechanic_portal')

    def _get_mechanic_partner(self):
        return request.env.user.partner_id.commercial_partner_id

    def _prepare_mechanic_request_domain(self, partner):
        return [('partner_id', 'child_of', [partner.commercial_partner_id.id])]

    def _prepare_mechanic_invoice_domain(self, partner):
        order_domain = self._prepare_orders_domain(partner)
        invoice_ids = request.env['sale.order'].search(order_domain).mapped('invoice_ids').ids
        return [
            ('id', 'in', invoice_ids or [0]),
            ('state', 'not in', ('draft', 'cancel')),
            ('move_type', 'in', ('out_invoice', 'out_refund', 'out_receipt')),
        ]

    def _prepare_mechanic_payment_allocation_domain(self, partner):
        return [
            ('partner_id', 'child_of', [partner.commercial_partner_id.id]),
            ('active', '=', True),
            ('payment_state', 'not in', ('draft', 'canceled', 'rejected')),
        ]

    def _prepare_quotations_domain(self, partner):
        if not self._is_mechanic_portal_user():
            return super()._prepare_quotations_domain(partner)
        return [
            ('mechanic_partner_id', 'child_of', [partner.commercial_partner_id.id]),
            ('state', '=', 'sent'),
        ]

    def _prepare_orders_domain(self, partner):
        if not self._is_mechanic_portal_user():
            return super()._prepare_orders_domain(partner)
        return [
            ('mechanic_partner_id', 'child_of', [partner.commercial_partner_id.id]),
            ('state', 'in', ['sale', 'done', 'cancel']),
        ]

    def _prepare_portal_layout_values(self):
        values = super()._prepare_portal_layout_values()
        values['is_mechanic_portal'] = self._is_mechanic_portal_user()
        return values

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if not self._is_mechanic_portal_user():
            return values

        partner = self._get_mechanic_partner()
        SaleOrder = request.env['sale.order']
        PortalRequest = request.env['mechanic.portal.request']
        AccountMove = request.env['account.move']
        PaymentAllocation = request.env['automotive.payment.allocation']

        if 'mechanic_order_count' in counters:
            values['mechanic_order_count'] = SaleOrder.search_count(self._prepare_orders_domain(partner))
        if 'mechanic_quote_count' in counters:
            values['mechanic_quote_count'] = SaleOrder.search_count(self._prepare_quotations_domain(partner))
        if 'mechanic_request_count' in counters and PortalRequest.has_access('read'):
            values['mechanic_request_count'] = PortalRequest.search_count(self._prepare_mechanic_request_domain(partner))
        if 'mechanic_invoice_count' in counters and AccountMove.has_access('read'):
            values['mechanic_invoice_count'] = AccountMove.search_count(self._prepare_mechanic_invoice_domain(partner))
        if 'mechanic_payment_count' in counters and PaymentAllocation.has_access('read'):
            values['mechanic_payment_count'] = PaymentAllocation.search_count(
                self._prepare_mechanic_payment_allocation_domain(partner)
            )

        return values

    @http.route(['/my/mechanic'], type='http', auth='user', website=True)
    def portal_my_mechanic(self, **kwargs):
        if not self._is_mechanic_portal_user():
            return request.redirect('/my/home')

        partner = self._get_mechanic_partner()
        SaleOrder = request.env['sale.order']
        PortalRequest = request.env['mechanic.portal.request']
        AccountMove = request.env['account.move']
        PaymentAllocation = request.env['automotive.payment.allocation']
        quote_domain = self._prepare_quotations_domain(partner)
        order_domain = self._prepare_orders_domain(partner)
        request_domain = self._prepare_mechanic_request_domain(partner)
        invoice_domain = self._prepare_mechanic_invoice_domain(partner)
        payment_domain = self._prepare_mechanic_payment_allocation_domain(partner)
        overdue_invoice_domain = invoice_domain + [
            ('payment_state', 'not in', ('in_payment', 'paid', 'reversed', 'blocked', 'invoicing_legacy')),
            ('invoice_date_due', '<', fields.Date.today()),
        ]

        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'mechanic',
            'mechanic_quote_count': SaleOrder.search_count(quote_domain),
            'mechanic_order_count': SaleOrder.search_count(order_domain),
            'mechanic_quotes': SaleOrder.search(quote_domain, order='date_order desc, id desc', limit=8),
            'mechanic_orders': SaleOrder.search(order_domain, order='date_order desc, id desc', limit=8),
            'mechanic_request_count': PortalRequest.search_count(request_domain) if PortalRequest.has_access('read') else 0,
            'mechanic_open_request_count': PortalRequest.search_count(request_domain + [('state', 'not in', ['done', 'cancelled'])]) if PortalRequest.has_access('read') else 0,
            'mechanic_requests': PortalRequest.search(request_domain, order='create_date desc, id desc', limit=6) if PortalRequest.has_access('read') else PortalRequest,
            'mechanic_balance': partner.current_balance,
            'mechanic_balance_currency': partner.currency_id or request.env.company.currency_id,
            'mechanic_automotive_balance': partner.automotive_balance_due,
            'mechanic_automotive_paid_total': partner.automotive_paid_total,
            'mechanic_invoice_count': AccountMove.search_count(invoice_domain) if AccountMove.has_access('read') else 0,
            'mechanic_overdue_invoice_count': AccountMove.search_count(overdue_invoice_domain) if AccountMove.has_access('read') else 0,
            'mechanic_invoices': AccountMove.search(invoice_domain, order='invoice_date desc, id desc', limit=6) if AccountMove.has_access('read') else AccountMove,
            'mechanic_payment_count': PaymentAllocation.search_count(payment_domain) if PaymentAllocation.has_access('read') else 0,
            'mechanic_payment_allocations': PaymentAllocation.search(payment_domain, order='payment_date desc, id desc', limit=6) if PaymentAllocation.has_access('read') else PaymentAllocation,
            'mechanic_status_counts': {
                'waiting_supply': SaleOrder.search_count(order_domain + [('auto_state', '=', 'waiting_supply')]),
                'partial_received': SaleOrder.search_count(order_domain + [('auto_state', '=', 'partial_received')]),
                'fully_received': SaleOrder.search_count(order_domain + [('auto_state', '=', 'fully_received')]),
                'ready_prep': SaleOrder.search_count(order_domain + [('auto_state', '=', 'ready_prep')]),
                'delivered': SaleOrder.search_count(order_domain + [('auto_state', '=', 'delivered')]),
                'cancel': SaleOrder.search_count(order_domain + [('auto_state', '=', 'cancel')]),
            },
            'mechanic_status_labels': {
                'waiting_supply': _('În așteptare aprovizionare'),
                'partial_received': _('Parțial recepționată'),
                'fully_received': _('Complet recepționată'),
                'ready_prep': _('Gata de pregătire'),
                'delivered': _('Livrată'),
                'cancel': _('Anulată'),
            },
        })
        return request.render('automotive_parts.portal_my_mechanic', values)

    @http.route(['/my/mechanic/payments', '/my/mechanic/payments/page/<int:page>'], type='http', auth='user', website=True)
    def portal_my_mechanic_payments(self, page=1, **kwargs):
        if not self._is_mechanic_portal_user():
            return request.redirect('/my/home')

        partner = self._get_mechanic_partner()
        PaymentAllocation = request.env['automotive.payment.allocation']
        if not PaymentAllocation.has_access('read'):
            return request.redirect('/my/mechanic')

        domain = self._prepare_mechanic_payment_allocation_domain(partner)
        total = PaymentAllocation.search_count(domain)
        pager = portal_pager(
            url='/my/mechanic/payments',
            total=total,
            page=page,
            step=self._items_per_page,
        )
        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'mechanic_payments',
            'mechanic_payment_allocations': PaymentAllocation.search(
                domain,
                order='payment_date desc, id desc',
                limit=self._items_per_page,
                offset=pager['offset'],
            ),
            'mechanic_automotive_balance': partner.automotive_balance_due,
            'mechanic_automotive_paid_total': partner.automotive_paid_total,
            'mechanic_balance_currency': partner.currency_id or request.env.company.currency_id,
            'pager': pager,
        })
        return request.render('automotive_parts.portal_my_mechanic_payments', values)

    @http.route(['/my/mechanic/requests', '/my/mechanic/requests/page/<int:page>'], type='http', auth='user', website=True)
    def portal_my_mechanic_requests(self, page=1, **kwargs):
        if not self._is_mechanic_portal_user():
            return request.redirect('/my/home')

        partner = self._get_mechanic_partner()
        PortalRequest = request.env['mechanic.portal.request']
        if not PortalRequest.has_access('read'):
            return request.redirect('/my/mechanic')

        domain = self._prepare_mechanic_request_domain(partner)
        total = PortalRequest.search_count(domain)
        pager = portal_pager(
            url='/my/mechanic/requests',
            total=total,
            page=page,
            step=self._items_per_page,
        )
        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'mechanic_requests',
            'mechanic_requests': PortalRequest.search(
                domain,
                order='create_date desc, id desc',
                limit=self._items_per_page,
                offset=pager['offset'],
            ),
            'pager': pager,
        })
        return request.render('automotive_parts.portal_my_mechanic_requests', values)

    @http.route(['/my/mechanic/requests/new'], type='http', auth='user', website=True)
    def portal_my_mechanic_request_form(self, **kwargs):
        if not self._is_mechanic_portal_user():
            return request.redirect('/my/home')

        partner = self._get_mechanic_partner()
        SaleOrder = request.env['sale.order']
        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'mechanic_request_form',
            'mechanic_orders_for_request': SaleOrder.search(self._prepare_orders_domain(partner), order='date_order desc, id desc', limit=50),
            'request_form_values': {
                'request_type': kwargs.get('request_type'),
                'sale_order_id': kwargs.get('sale_order_id'),
                'description': kwargs.get('description'),
            },
            'request_error': kwargs.get('request_error'),
        })
        return request.render('automotive_parts.portal_my_mechanic_request_form', values)

    @http.route(['/my/mechanic/requests/create'], type='http', auth='user', website=True, methods=['POST'], csrf=True)
    def portal_create_mechanic_request(self, **post):
        if not self._is_mechanic_portal_user():
            return request.redirect('/my/home')

        partner = self._get_mechanic_partner()
        SaleOrder = request.env['sale.order']
        PortalRequest = request.env['mechanic.portal.request']

        request_type = (post.get('request_type') or 'general').strip()
        description = (post.get('description') or '').strip()
        sale_order_id = post.get('sale_order_id')
        allowed_types = {'order', 'document', 'payment', 'general'}
        if request_type not in allowed_types or not description:
            return self.portal_my_mechanic_request_form(
                request_type=request_type,
                sale_order_id=sale_order_id,
                description=description,
                request_error=_('Select a valid request type and provide a description.'),
            )

        sale_order = SaleOrder.browse(int(sale_order_id)) if sale_order_id and sale_order_id.isdigit() else SaleOrder.browse()
        if sale_order and sale_order not in SaleOrder.search(self._prepare_orders_domain(partner)):
            return request.redirect('/my/mechanic/requests/new')

        request_record = PortalRequest.create({
            'partner_id': partner.id,
            'request_user_id': request.env.user.id,
            'sale_order_id': sale_order.id if sale_order else False,
            'request_type': request_type,
            'description': description,
        })
        return request.redirect(request_record.access_url)

    @http.route(['/my/mechanic/requests/<int:request_id>'], type='http', auth='user', website=True)
    def portal_my_mechanic_request_detail(self, request_id, **kwargs):
        if not self._is_mechanic_portal_user():
            return request.redirect('/my/home')

        partner = self._get_mechanic_partner()
        request_record = request.env['mechanic.portal.request'].search(
            self._prepare_mechanic_request_domain(partner) + [('id', '=', request_id)],
            limit=1,
        )
        if not request_record:
            return request.redirect('/my/mechanic/requests')

        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'mechanic_request_detail',
            'mechanic_request': request_record,
            'mechanic_request_messages': request_record.message_ids.filtered(
                lambda message: (
                    message.message_type in ('comment', 'email')
                    and (not message.subtype_id or not message.subtype_id.internal)
                )
            ).sorted(lambda message: message.date or fields.Datetime.now()),
        })
        return request.render('automotive_parts.portal_my_mechanic_request_detail', values)
