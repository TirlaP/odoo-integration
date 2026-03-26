# -*- coding: utf-8 -*-
from odoo import http, _
from odoo.http import request

from odoo.addons.sale.controllers.portal import CustomerPortal as SaleCustomerPortal


class CustomerPortal(SaleCustomerPortal):
    """Mechanic-facing portal extensions built on top of standard sale portal."""

    def _is_mechanic_portal_user(self):
        return request.env.user.has_group('automotive_parts.group_mechanic_portal')

    def _prepare_portal_layout_values(self):
        values = super()._prepare_portal_layout_values()
        values['is_mechanic_portal'] = self._is_mechanic_portal_user()
        return values

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if not self._is_mechanic_portal_user():
            return values

        partner = request.env.user.partner_id
        SaleOrder = request.env['sale.order']

        if 'mechanic_order_count' in counters:
            values['mechanic_order_count'] = SaleOrder.search_count(self._prepare_orders_domain(partner))
        if 'mechanic_quote_count' in counters:
            values['mechanic_quote_count'] = SaleOrder.search_count(self._prepare_quotations_domain(partner))

        return values

    @http.route(['/my/mechanic'], type='http', auth='user', website=True)
    def portal_my_mechanic(self, **kwargs):
        if not self._is_mechanic_portal_user():
            return request.redirect('/my/home')

        partner = request.env.user.partner_id
        SaleOrder = request.env['sale.order']
        quote_domain = self._prepare_quotations_domain(partner)
        order_domain = self._prepare_orders_domain(partner)

        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'mechanic',
            'mechanic_quote_count': SaleOrder.search_count(quote_domain),
            'mechanic_order_count': SaleOrder.search_count(order_domain),
            'mechanic_quotes': SaleOrder.search(quote_domain, order='date_order desc', limit=8).sudo(),
            'mechanic_orders': SaleOrder.search(order_domain, order='date_order desc', limit=8).sudo(),
            'mechanic_status_counts': {
                'waiting_supply': SaleOrder.search_count(order_domain + [('auto_state', '=', 'waiting_supply')]),
                'partial_received': SaleOrder.search_count(order_domain + [('auto_state', '=', 'partial_received')]),
                'fully_received': SaleOrder.search_count(order_domain + [('auto_state', '=', 'fully_received')]),
                'ready_prep': SaleOrder.search_count(order_domain + [('auto_state', '=', 'ready_prep')]),
                'delivered': SaleOrder.search_count(order_domain + [('auto_state', '=', 'delivered')]),
            },
            'mechanic_status_labels': {
                'waiting_supply': _('În așteptare aprovizionare'),
                'partial_received': _('Parțial recepționată'),
                'fully_received': _('Complet recepționată'),
                'ready_prep': _('Gata de pregătire'),
                'delivered': _('Livrată'),
            },
        })
        return request.render('automotive_parts.portal_my_mechanic', values)
