# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request


class InvoiceIngestReactController(http.Controller):
    def _get_job(self, job_id):
        job = request.env['invoice.ingest.job'].browse(int(job_id)).exists()
        if not job:
            return request.env['invoice.ingest.job']
        job.check_access_rights('read')
        job.check_access_rule('read')
        return job

    def _serialize_line(self, line):
        return {
            'id': line.id,
            'sequence': line.sequence,
            'product_code': line.product_code or '',
            'product_code_raw': line.product_code_raw or '',
            'supplier_brand': line.supplier_brand or '',
            'supplier_brand_id': line.supplier_brand_id or False,
            'product_description': line.product_description or '',
            'quantity': line.quantity or 0.0,
            'unit_price': line.unit_price or 0.0,
            'discount_percent': line.discount_percent or 0.0,
            'discounted_unit_price': line.discounted_unit_price or 0.0,
            'unit_price_incl_vat': line.unit_price_incl_vat or 0.0,
            'vat_rate': line.vat_rate or 0.0,
            'vat_unit_amount': line.vat_unit_amount or 0.0,
            'subtotal': line.subtotal or 0.0,
            'subtotal_incl_vat': line.subtotal_incl_vat or 0.0,
            'markup_percent': line.markup_percent or 0.0,
            'markup_amount': line.markup_amount or 0.0,
            'sale_price_excl_vat': line.sale_price_excl_vat or 0.0,
            'sale_price_incl_vat': line.sale_price_incl_vat or 0.0,
            'product_id': line.product_id.id if line.product_id else False,
            'product_display_name': line.product_id.display_name if line.product_id else '',
            'matched_ean': line.matched_ean or '',
            'matched_internal_code': line.matched_internal_code or '',
            'match_status': line.match_status or 'not_found',
            'match_method': line.match_method or '',
            'match_confidence': line.match_confidence or 0.0,
        }

    def _serialize_job(self, job):
        return {
            'id': job.id,
            'name': job.name,
            'source': job.source,
            'state': job.state,
            'partner_id': job.partner_id.id if job.partner_id else False,
            'partner_name': job.partner_id.display_name if job.partner_id else '',
            'invoice_number': job.invoice_number or '',
            'invoice_date': str(job.invoice_date) if job.invoice_date else '',
            'vat_rate': job.vat_rate or 0.0,
            'amount_total': job.amount_total or 0.0,
            'currency_symbol': job.currency_id.symbol or '',
            'currency_position': job.currency_id.position or 'after',
            'ai_confidence': job.ai_confidence or 0.0,
            'attachment_name': job.attachment_id.name if job.attachment_id else '',
            'external_id': job.external_id or '',
            'error': job.error or '',
            'payload_json': job.payload_json or '',
            'lines': [self._serialize_line(line) for line in job.line_ids.sorted('sequence')],
        }

    @http.route(
        [
            '/automotive/invoice-ingest/react/<int:job_id>',
            '/odoo/automotive-invoice-ingest/react/<int:job_id>',
            '/odoo/automotive/invoice-ingest/react/<int:job_id>',
        ],
        type='http',
        auth='user',
    )
    def invoice_ingest_react_page(self, job_id, **kwargs):
        job = self._get_job(job_id)
        if not job:
            return request.not_found()
        return request.render('automotive_parts.invoice_ingest_react_page', {
            'job_id': job.id,
        })

    @http.route(
        ['/automotive/invoice-ingest/react', '/odoo/automotive/invoice-ingest/react'],
        type='http',
        auth='user',
    )
    def invoice_ingest_react_page_root(self, **kwargs):
        return request.redirect('/odoo/automotive-invoice-ingest')

    @http.route('/automotive/invoice-ingest/react/data', type='json', auth='user', csrf=False)
    def invoice_ingest_react_data(self, job_id):
        job = self._get_job(job_id)
        if not job:
            return {'ok': False, 'error': 'Job not found'}
        return {'ok': True, 'job': self._serialize_job(job)}

    @http.route('/automotive/invoice-ingest/react/line/update', type='json', auth='user', csrf=False)
    def invoice_ingest_react_line_update(self, line_id, values):
        line = request.env['invoice.ingest.job.line'].browse(int(line_id)).exists()
        if not line:
            return {'ok': False, 'error': 'Line not found'}
        line.check_access_rights('write')
        line.check_access_rule('write')

        allowed_float_fields = {
            'quantity', 'unit_price', 'discount_percent', 'vat_rate', 'markup_percent'
        }
        allowed_text_fields = {'product_code', 'product_code_raw', 'supplier_brand', 'product_description'}
        allowed_m2o_fields = {'product_id'}
        vals = {}

        for key, value in (values or {}).items():
            if key in allowed_float_fields:
                try:
                    vals[key] = float(value or 0.0)
                except Exception:
                    vals[key] = 0.0
            elif key in allowed_text_fields:
                vals[key] = (value or '').strip()
            elif key in allowed_m2o_fields:
                if value:
                    vals[key] = int(value)
                else:
                    vals[key] = False

        if line.job_id and any(name in vals for name in ('product_code', 'product_code_raw', 'supplier_brand', 'product_description')):
            parsed = line.job_id._parse_invoice_line_identity(
                vals.get('product_code_raw') or vals.get('product_code') or line.product_code_raw or line.product_code,
                product_description=vals.get('product_description') or line.product_description,
                supplier_hint=vals.get('supplier_brand') or line.supplier_brand,
            )
            if parsed.get('product_code_primary'):
                vals['product_code'] = parsed['product_code_primary']
            if parsed.get('product_code_raw'):
                vals['product_code_raw'] = parsed['product_code_raw']
            if parsed.get('supplier_brand'):
                vals['supplier_brand'] = parsed['supplier_brand']

        if vals:
            line.write(vals)
            line.flush_recordset()

        return {'ok': True, 'line': self._serialize_line(line)}

    @http.route('/automotive/invoice-ingest/react/line/action', type='json', auth='user', csrf=False)
    def invoice_ingest_react_line_action(self, line_id, action_name):
        line = request.env['invoice.ingest.job.line'].browse(int(line_id)).exists()
        if not line:
            return {'ok': False, 'error': 'Line not found'}
        line.check_access_rights('write')
        line.check_access_rule('write')

        if action_name == 'try_match':
            line.action_try_match()
        elif action_name == 'clear_match':
            line.action_clear_match()
        else:
            return {'ok': False, 'error': 'Unsupported action'}

        line.flush_recordset()
        return {'ok': True, 'line': self._serialize_line(line)}
