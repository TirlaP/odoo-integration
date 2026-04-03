# -*- coding: utf-8 -*-
import base64

from odoo import http, _, fields
from odoo.exceptions import ValidationError
from odoo.http import content_disposition, request
from werkzeug.exceptions import NotFound

from odoo.addons.sale.controllers.portal import CustomerPortal as SaleCustomerPortal
from odoo.addons.portal.controllers.portal import pager as portal_pager


class CustomerPortal(SaleCustomerPortal):
    """Mechanic-facing portal extensions built on top of standard sale portal."""
    _MECHANIC_DOCUMENT_FILTERS = ('all', 'invoices', 'deliveries', 'archived')
    _MECHANIC_DOCUMENT_LABELS = {
        'invoice': _('Invoice'),
        'delivery': _('Delivery'),
        'archived': _('Archived'),
    }

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

    def _prepare_mechanic_delivery_domain(self, partner):
        delivery_ids = self._get_mechanic_delivery_ids(partner)
        return [('id', 'in', delivery_ids or [0])]

    def _get_mechanic_delivery_ids(self, partner):
        """Resolve outgoing delivery ids without requiring portal access to picking types."""
        return request.env['stock.picking'].sudo().search([
            ('sale_id.mechanic_partner_id', 'child_of', [partner.commercial_partner_id.id]),
            ('picking_type_id.code', '=', 'outgoing'),
        ]).ids

    def _prepare_mechanic_document_archive_domain(self, partner):
        return [
            ('state', '=', 'archived'),
            ('partner_id', 'child_of', [partner.commercial_partner_id.id]),
        ]

    def _get_mechanic_document_counts(self, partner):
        AccountMove = request.env['account.move']
        DeliveryPicking = request.env['stock.picking']
        CommercialDocumentArchive = request.env['commercial.document.archive']

        invoice_count = AccountMove.search_count(self._prepare_mechanic_invoice_domain(partner)) if AccountMove.has_access('read') else 0
        delivery_count = DeliveryPicking.search_count(self._prepare_mechanic_delivery_domain(partner)) if DeliveryPicking.has_access('read') else 0
        archived_count = CommercialDocumentArchive.search_count(self._prepare_mechanic_document_archive_domain(partner)) if CommercialDocumentArchive.has_access('read') else 0
        return {
            'invoices': invoice_count,
            'deliveries': delivery_count,
            'archived': archived_count,
            'all': invoice_count + delivery_count + archived_count,
        }

    def _normalize_mechanic_document_filter(self, doc_filter):
        return doc_filter if doc_filter in self._MECHANIC_DOCUMENT_FILTERS else 'all'

    def _build_mechanic_document_entries(self, partner, doc_filter='all', limit=None):
        AccountMove = request.env['account.move']
        DeliveryPicking = request.env['stock.picking']
        CommercialDocumentArchive = request.env['commercial.document.archive']
        doc_filter = self._normalize_mechanic_document_filter(doc_filter)
        entries = []

        if doc_filter in ('all', 'invoices') and AccountMove.has_access('read'):
            invoices = AccountMove.search(
                self._prepare_mechanic_invoice_domain(partner),
                order='invoice_date desc, id desc',
            )
            for invoice in invoices:
                entries.append({
                    'kind': 'invoice',
                    'label': self._MECHANIC_DOCUMENT_LABELS['invoice'],
                    'name': invoice.name or invoice.ref or invoice.display_name,
                    'date': invoice.invoice_date,
                    'url': invoice.get_portal_url(),
                    'status': invoice.payment_state or invoice.state or '-',
                    'sort_date': invoice.invoice_date or fields.Date.today(),
                })

        if doc_filter in ('all', 'deliveries') and DeliveryPicking.has_access('read'):
            deliveries = DeliveryPicking.search(
                self._prepare_mechanic_delivery_domain(partner),
                order='scheduled_date desc, id desc',
            )
            for delivery in deliveries:
                entries.append({
                    'kind': 'delivery',
                    'label': self._MECHANIC_DOCUMENT_LABELS['delivery'],
                    'name': delivery.name,
                    'date': delivery.scheduled_date.date() if delivery.scheduled_date else False,
                    'url': False,
                    'status': delivery.state or '-',
                    'sort_date': delivery.scheduled_date or fields.Datetime.now(),
                })

        if doc_filter in ('all', 'archived') and CommercialDocumentArchive.has_access('read'):
            archives = CommercialDocumentArchive.search(
                self._prepare_mechanic_document_archive_domain(partner),
                order='archived_at desc, id desc',
            )
            for archive in archives:
                entries.append({
                    'kind': 'archived',
                    'label': self._MECHANIC_DOCUMENT_LABELS['archived'],
                    'name': archive.name,
                    'date': archive.archived_at.date() if archive.archived_at else False,
                    'url': f'/my/mechanic/documents/{archive.id}',
                    'status': dict(archive._fields['document_type'].selection).get(archive.document_type) or archive.document_type,
                    'sort_date': archive.archived_at or fields.Datetime.now(),
                })

        entries.sort(key=lambda entry: entry['sort_date'] or fields.Datetime.now(), reverse=True)
        return entries[:limit] if limit else entries

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
        DeliveryPicking = request.env['stock.picking']
        CommercialDocumentArchive = request.env['commercial.document.archive']

        if 'mechanic_order_count' in counters:
            values['mechanic_order_count'] = SaleOrder.search_count(self._prepare_orders_domain(partner))
        if 'mechanic_quote_count' in counters:
            values['mechanic_quote_count'] = SaleOrder.search_count(self._prepare_quotations_domain(partner))
        if 'mechanic_request_count' in counters and PortalRequest.has_access('read'):
            values['mechanic_request_count'] = PortalRequest.search_count(self._prepare_mechanic_request_domain(partner))
        if 'mechanic_invoice_count' in counters and AccountMove.has_access('read'):
            values['mechanic_invoice_count'] = AccountMove.search_count(self._prepare_mechanic_invoice_domain(partner))
        if 'mechanic_delivery_count' in counters and DeliveryPicking.has_access('read'):
            values['mechanic_delivery_count'] = DeliveryPicking.search_count(self._prepare_mechanic_delivery_domain(partner))
        if 'mechanic_document_count' in counters and CommercialDocumentArchive.has_access('read'):
            values['mechanic_document_count'] = self._get_mechanic_document_counts(partner)['all']
        return values

    @http.route(['/my/mechanic'], type='http', auth='user', website=True)
    def portal_my_mechanic(self, **kwargs):
        if not self._is_mechanic_portal_user():
            return request.redirect('/my/home')

        partner = self._get_mechanic_partner()
        SaleOrder = request.env['sale.order']
        PortalRequest = request.env['mechanic.portal.request']
        AccountMove = request.env['account.move']
        DeliveryPicking = request.env['stock.picking']
        CommercialDocumentArchive = request.env['commercial.document.archive']
        quote_domain = self._prepare_quotations_domain(partner)
        order_domain = self._prepare_orders_domain(partner)
        request_domain = self._prepare_mechanic_request_domain(partner)
        invoice_domain = self._prepare_mechanic_invoice_domain(partner)
        delivery_domain = self._prepare_mechanic_delivery_domain(partner)
        archive_domain = self._prepare_mechanic_document_archive_domain(partner)
        overdue_invoice_domain = invoice_domain + [
            ('payment_state', 'not in', ('in_payment', 'paid', 'reversed', 'blocked', 'invoicing_legacy')),
            ('invoice_date_due', '<', fields.Date.today()),
        ]
        document_counts = self._get_mechanic_document_counts(partner)

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
            'mechanic_invoice_count': AccountMove.search_count(invoice_domain) if AccountMove.has_access('read') else 0,
            'mechanic_overdue_invoice_count': AccountMove.search_count(overdue_invoice_domain) if AccountMove.has_access('read') else 0,
            'mechanic_invoices': AccountMove.search(invoice_domain, order='invoice_date desc, id desc', limit=6) if AccountMove.has_access('read') else AccountMove,
            'mechanic_delivery_count': DeliveryPicking.search_count(delivery_domain) if DeliveryPicking.has_access('read') else 0,
            'mechanic_deliveries': DeliveryPicking.search(delivery_domain, order='scheduled_date desc, id desc', limit=6) if DeliveryPicking.has_access('read') else DeliveryPicking,
            'mechanic_document_count': document_counts['all'],
            'mechanic_document_counts': document_counts,
            'mechanic_archived_documents': CommercialDocumentArchive.search(
                archive_domain,
                order='archived_at desc, id desc',
                limit=8,
            ) if CommercialDocumentArchive.has_access('read') else CommercialDocumentArchive,
            'mechanic_recent_documents': self._build_mechanic_document_entries(partner, limit=6),
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

    @http.route(['/my/mechanic/documents', '/my/mechanic/documents/page/<int:page>'], type='http', auth='user', website=True)
    def portal_my_mechanic_documents(self, page=1, **kwargs):
        if not self._is_mechanic_portal_user():
            return request.redirect('/my/home')

        partner = self._get_mechanic_partner()
        AccountMove = request.env['account.move']
        DeliveryPicking = request.env['stock.picking']
        CommercialDocumentArchive = request.env['commercial.document.archive']

        if not (AccountMove.has_access('read') or DeliveryPicking.has_access('read') or CommercialDocumentArchive.has_access('read')):
            return request.redirect('/my/mechanic')

        doc_filter = self._normalize_mechanic_document_filter(kwargs.get('doc_type'))
        document_counts = self._get_mechanic_document_counts(partner)
        document_entries = self._build_mechanic_document_entries(partner, doc_filter=doc_filter)
        total = len(document_entries)
        pager = portal_pager(
            url='/my/mechanic/documents',
            total=total,
            page=page,
            step=self._items_per_page,
            url_args={'doc_type': doc_filter} if doc_filter != 'all' else {},
        )
        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'mechanic_documents',
            'mechanic_document_filter': doc_filter,
            'mechanic_document_filters': self._MECHANIC_DOCUMENT_FILTERS,
            'mechanic_document_count': document_counts['all'],
            'mechanic_document_counts': document_counts,
            'mechanic_documents': document_entries[pager['offset']:pager['offset'] + self._items_per_page],
            'pager': pager,
        })
        return request.render('automotive_parts.portal_my_mechanic_documents', values)

    @http.route(['/my/mechanic/documents/<int:document_id>'], type='http', auth='user', website=True)
    def portal_my_mechanic_document_detail(self, document_id, **kwargs):
        if not self._is_mechanic_portal_user():
            return request.redirect('/my/home')

        partner = self._get_mechanic_partner()
        CommercialDocumentArchive = request.env['commercial.document.archive']
        if not CommercialDocumentArchive.has_access('read'):
            return request.redirect('/my/mechanic')

        archive = CommercialDocumentArchive.search(
            self._prepare_mechanic_document_archive_domain(partner) + [('id', '=', document_id)],
            limit=1,
        )
        if not archive:
            raise NotFound()

        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'mechanic_document_detail',
            'mechanic_document': archive,
        })
        return request.render('automotive_parts.portal_my_mechanic_document_detail', values)

    @http.route(['/my/mechanic/documents/<int:document_id>/attachment'], type='http', auth='user', website=True)
    def portal_my_mechanic_document_attachment(self, document_id, **kwargs):
        if not self._is_mechanic_portal_user():
            return request.redirect('/my/home')

        partner = self._get_mechanic_partner()
        CommercialDocumentArchive = request.env['commercial.document.archive']
        if not CommercialDocumentArchive.has_access('read'):
            return request.redirect('/my/mechanic')

        archive = CommercialDocumentArchive.search(
            self._prepare_mechanic_document_archive_domain(partner) + [('id', '=', document_id)],
            limit=1,
        )
        if not archive or not archive.attachment_id:
            raise NotFound()

        attachment = archive.attachment_id.sudo()
        file_data = base64.b64decode(attachment.datas or b'')
        headers = [
            ('Content-Type', attachment.mimetype or 'application/octet-stream'),
            ('Content-Length', str(len(file_data))),
            ('Content-Disposition', content_disposition(archive.attachment_name or attachment.name or 'document')),
        ]
        return request.make_response(file_data, headers=headers)

    @http.route(['/my/mechanic/payments', '/my/mechanic/payments/page/<int:page>'], type='http', auth='user', website=True)
    def portal_my_mechanic_payments(self, page=1, **kwargs):
        return request.redirect('/my/mechanic/documents')

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
            'reply_feedback': kwargs.get('reply'),
            'mechanic_request_messages': request_record.message_ids.filtered(
                lambda message: (
                    message.message_type in ('comment', 'email')
                    and (not message.subtype_id or not message.subtype_id.internal)
                )
            ).sorted(lambda message: message.date or fields.Datetime.now()),
        })
        return request.render('automotive_parts.portal_my_mechanic_request_detail', values)

    @http.route(['/my/mechanic/requests/<int:request_id>/message'], type='http', auth='user', website=True, methods=['POST'], csrf=True)
    def portal_my_mechanic_request_message(self, request_id, **post):
        if not self._is_mechanic_portal_user():
            return request.redirect('/my/home')

        partner = self._get_mechanic_partner()
        request_record = request.env['mechanic.portal.request'].search(
            self._prepare_mechanic_request_domain(partner) + [('id', '=', request_id)],
            limit=1,
        )
        if not request_record:
            return request.redirect('/my/mechanic/requests')

        reply_code = 'posted'
        try:
            request_record.action_portal_reply(post.get('message'))
        except ValidationError:
            reply_code = 'closed' if request_record.state in ('done', 'cancelled') else 'invalid'
        return request.redirect(f'{request_record.access_url}?reply={reply_code}')
