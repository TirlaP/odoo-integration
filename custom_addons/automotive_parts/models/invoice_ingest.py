# -*- coding: utf-8 -*-
import base64
import glob
import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime
from math import ceil
from io import BytesIO
from collections import defaultdict
from xml.etree import ElementTree

import requests
from PyPDF2 import PdfReader
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.osv import expression

INVOICE_META_PREFIXES = ('NC=', 'CPV=')
INVOICE_SUPPLIER_NOISE_TOKENS = {
    'OEM', 'AM', 'OE', 'OES', 'AFTERMARKET',
    'BC', 'BUC', 'PCS', 'SET', 'PIECE', 'PIESE',
    'NC', 'CPV',
}
INVOICE_CODE_STOP_WORDS = {
    'SET', 'FILTRU', 'CUREA', 'BECURI', 'INTINZATOR', 'STERGATOR',
    'TERMOSTAT', 'BLISTER', 'DE', 'CU', 'SI', 'LA', 'PENTRU', 'TIP',
}
INVOICE_TRIM_SUFFIXES = ('CT', 'V')
AUTO_MATCH_CONFIDENCE_THRESHOLD = 88.0
PROGRESSIVE_TRIM_MIN_LEN = 5
PROGRESSIVE_TRIM_MAX_STEPS = 8

_logger = logging.getLogger(__name__)


class InvoiceIngestJob(models.Model):
    _name = 'invoice.ingest.job'
    _description = 'Invoice Ingest Job'
    _order = 'id desc'
    _AUDIT_FIELDS = {
        'name',
        'source',
        'state',
        'picking_id',
        'attachment_id',
        'partner_id',
        'invoice_number',
        'invoice_date',
        'currency_id',
        'amount_total',
        'vat_rate',
        'document_type',
        'external_id',
        'account_move_id',
        'error',
        'ai_model',
        'ai_confidence',
        'batch_uid',
        'batch_name',
        'batch_index',
        'batch_total',
        'queued_at',
        'started_at',
        'finished_at',
    }
    _AUDIT_WRITE_FIELDS = {
        'name',
        'source',
        'picking_id',
        'attachment_id',
        'partner_id',
        'invoice_number',
        'invoice_date',
        'currency_id',
        'amount_total',
        'vat_rate',
        'document_type',
        'external_id',
        'account_move_id',
        'ai_model',
    }
    _sql_constraints = [
        (
            'invoice_ingest_source_external_unique',
            'unique(source, external_id)',
            'This ANAF/OCR document was already imported.',
        )
    ]

    name = fields.Char(required=True, default=lambda self: f"Invoice Ingest {fields.Datetime.now()}")
    source = fields.Selection(
        [
            ('manual', 'Manual'),
            ('anaf', 'ANAF e-Factura'),
            ('ocr', 'OCR/AI (PDF/Image)'),
        ],
        default='manual',
        required=True,
        index=True,
    )
    state = fields.Selection(
        [
            ('pending', 'Pending'),
            ('running', 'Running'),
            ('needs_review', 'Needs Review'),
            ('done', 'Done'),
            ('failed', 'Failed'),
        ],
        default='pending',
        index=True,
    )

    picking_id = fields.Many2one('stock.picking', string='Reception (Optional)')
    attachment_id = fields.Many2one('ir.attachment', string='Invoice File (PDF/Image)')

    partner_id = fields.Many2one('res.partner', string='Supplier')
    invoice_number = fields.Char('Invoice Number')
    invoice_date = fields.Date('Invoice Date')
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id)
    amount_total = fields.Monetary('Total Amount', currency_field='currency_id')
    vat_rate = fields.Float('VAT Rate (%)')
    document_type = fields.Selection(
        [('invoice', 'Invoice'), ('credit_note', 'Credit Note')],
        string='Document Type',
        default='invoice',
        index=True,
    )

    external_id = fields.Char('External ID', help='ANAF message/document identifier (when source=ANAF).')
    payload_json = fields.Text('Payload JSON')

    account_move_id = fields.Many2one('account.move', string='Vendor Bill')
    error = fields.Text()
    line_ids = fields.One2many('invoice.ingest.job.line', 'job_id', string='Extracted Lines')
    receipt_sync_state = fields.Selection(
        [
            ('not_ready', 'Not Ready'),
            ('not_synced', 'Not Synced'),
            ('in_progress', 'In Progress'),
            ('needs_review', 'Needs Review'),
            ('synced', 'Synced'),
            ('cancelled', 'Cancelled'),
        ],
        string='Receipt Sync Status',
        compute='_compute_receipt_sync_state',
    )
    ai_model = fields.Char(
        'AI Model',
        default=lambda self: self._default_ai_model(),
        help='Used when extracting invoice details with OpenAI.',
    )
    ai_confidence = fields.Float('AI Confidence (%)')
    batch_uid = fields.Char('Batch UID', index=True, readonly=True)
    batch_name = fields.Char('Batch Name', index=True, readonly=True)
    batch_index = fields.Integer('Batch Index', readonly=True)
    batch_total = fields.Integer('Batch Total', readonly=True)
    queued_at = fields.Datetime('Queued At', readonly=True, index=True)
    started_at = fields.Datetime('Started At', readonly=True, index=True)
    finished_at = fields.Datetime('Finished At', readonly=True, index=True)
    line_extraction_message = fields.Text(
        'Line Extraction Message',
        compute='_compute_line_extraction_message',
    )

    def _audit_snapshot(self, field_names=None):
        self.ensure_one()
        tracked_fields = field_names or self._AUDIT_FIELDS
        snapshot = {}
        for field_name in tracked_fields:
            if field_name not in self._fields:
                continue
            value = self[field_name]
            if isinstance(value, models.BaseModel):
                snapshot[field_name] = value.ids
            else:
                snapshot[field_name] = value
        return snapshot

    @api.depends('line_ids', 'state', 'source', 'attachment_id')
    def _compute_line_extraction_message(self):
        for job in self:
            if job.line_ids:
                job.line_extraction_message = False
            elif job.state == 'running':
                job.line_extraction_message = _(
                    'Invoice extraction is running in the background.'
                )
            elif job.state == 'pending':
                job.line_extraction_message = _(
                    'Invoice extraction is queued and will start automatically.'
                )
            elif job.source == 'ocr' and job.attachment_id and job.state in {'needs_review', 'done'}:
                job.line_extraction_message = _(
                    'No invoice lines were extracted from this document. Review the header values or retry with a clearer PDF/image.'
                )
            elif job.state == 'failed' and job.error:
                job.line_extraction_message = _(
                    'Invoice extraction failed. Check the error field and retry the import.'
                )
            else:
                job.line_extraction_message = False

    def _audit_line_summary(self):
        self.ensure_one()
        matched_lines = self.line_ids.filtered(lambda line: bool(line.product_id))
        manual_lines = self.line_ids.filtered(lambda line: line.match_status == 'manual')
        unmatched_lines = self.line_ids.filtered(lambda line: not line.product_id)
        return {
            'line_count': len(self.line_ids),
            'matched_lines': len(matched_lines),
            'manual_review_lines': len(manual_lines),
            'unmatched_lines': len(unmatched_lines),
        }

    def _audit_job_summary(self):
        self.ensure_one()
        summary = self._audit_snapshot()
        summary.update(self._audit_line_summary())
        summary.update({
            'receipt_sync_state': self.receipt_sync_state,
        })
        return summary

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

    def _audit_blocked_action(self, description, reason, old_values=None):
        self.ensure_one()
        self._audit_log(
            action='custom',
            description=description,
            old_values=old_values,
            new_values={
                'blocked_reason': reason,
                'state': self.state,
                'receipt_sync_state': self.receipt_sync_state,
                'account_move_id': self.account_move_id.id if self.account_move_id else False,
                'picking_id': self.picking_id.id if self.picking_id else False,
                **self._audit_line_summary(),
            },
        )

    def _queue_metadata(self, batch_uid=None, batch_name=None, batch_index=None, batch_total=None):
        self.ensure_one()
        return {
            'state': 'pending',
            'error': False,
            'queued_at': fields.Datetime.now(),
            'started_at': False,
            'finished_at': False,
            'batch_uid': batch_uid or self.batch_uid or uuid.uuid4().hex,
            'batch_name': batch_name or self.batch_name or False,
            'batch_index': batch_index if batch_index not in (None, False) else (self.batch_index or 0),
            'batch_total': batch_total if batch_total not in (None, False) else (self.batch_total or 0),
        }

    def _queue_processing(self, batch_uid=None, batch_name=None, batch_index=None, batch_total=None):
        self.ensure_one()
        values = self._queue_metadata(
            batch_uid=batch_uid,
            batch_name=batch_name,
            batch_index=batch_index,
            batch_total=batch_total,
        )
        self.write(values)
        return values['batch_uid']

    def _get_async_processing_job(self, states=('queued', 'running')):
        self.ensure_one()
        return self.env['automotive.async.job'].search(
            [
                ('job_type', '=', 'invoice_ingest'),
                ('source_model', '=', self._name),
                ('source_res_id', '=', self.id),
                ('target_model', '=', self._name),
                ('target_res_id', '=', self.id),
                ('target_method', '=', '_process_ingest_job'),
                ('state', 'in', list(states)),
            ],
            order='id desc',
            limit=1,
        )

    def _enqueue_async_processing(self, batch=False, batch_uid=None, batch_name=None, force=False, priority=80):
        self.ensure_one()
        existing_job = self._get_async_processing_job()
        if existing_job and not force:
            if batch and not existing_job.batch_id:
                existing_job.sudo().write({'batch_id': batch.id})
            return existing_job

        effective_batch_name = batch_name or self.batch_name or self.display_name
        self._queue_processing(batch_uid=batch_uid, batch_name=effective_batch_name)
        return self.env['automotive.async.job'].enqueue_job(
            'invoice_ingest',
            name=_('Process %s') % self.display_name,
            payload={
                'invoice_ingest_job_id': self.id,
                'batch_uid': self.batch_uid,
                'batch_name': self.batch_name,
            },
            source=self,
            batch=batch,
            batch_name=effective_batch_name,
            priority=priority,
            target_model=self._name,
            target_method='_process_ingest_job',
            target_res_id=self.id,
        )

    @api.model_create_multi
    def create(self, vals_list):
        now = fields.Datetime.now()
        for vals in vals_list:
            if vals.get('state', 'pending') == 'pending' and not vals.get('queued_at'):
                vals['queued_at'] = now
        jobs = super().create(vals_list)
        if self.env.context.get('skip_audit_log') is True:
            return jobs

        for job, vals in zip(jobs, vals_list):
            tracked_fields = [field_name for field_name in vals.keys() if field_name in job._AUDIT_FIELDS]
            job._audit_log(
                action='create',
                description=f'Invoice ingest job created: {job.display_name}',
                new_values=job._audit_snapshot(tracked_fields),
            )
        return jobs

    def write(self, vals):
        context = dict(self.env.context or {})
        tracked_fields = [field_name for field_name in vals.keys() if field_name in self._AUDIT_WRITE_FIELDS]
        old_by_id = {}
        state_before = {}
        if tracked_fields and context.get('skip_audit_log') is not True:
            old_by_id = {
                job.id: job._audit_snapshot(tracked_fields)
                for job in self
            }
        if 'state' in vals and context.get('skip_audit_log') is not True:
            state_before = {job.id: job.state for job in self}

        result = super().write(vals)

        if tracked_fields and context.get('skip_audit_log') is not True:
            for job in self:
                job._audit_log(
                    action='write',
                    description=f'Invoice ingest job updated: {job.display_name}',
                    old_values=old_by_id.get(job.id),
                    new_values=job._audit_snapshot(tracked_fields),
                )

        if 'state' in vals and context.get('skip_audit_log') is not True:
            for job in self:
                old_state = state_before.get(job.id)
                if old_state == job.state:
                    continue
                job._audit_log(
                    action='custom',
                    description=f'Invoice ingest state changed: {old_state or "unknown"} -> {job.state}',
                    old_values={
                        'state': old_state,
                    },
                    new_values={
                        'state': job.state,
                        'error': job.error,
                        'receipt_sync_state': job.receipt_sync_state,
                    },
                )

        return result

    def action_open_upload_wizard(self, *args, **kwargs):
        self.ensure_one()
        return {
            'name': 'Import AI facturi',
            'type': 'ir.actions.act_window',
            'res_model': 'invoice.ingest.upload.wizard',
            'view_mode': 'form',
            'target': 'new',
        }

    @api.depends(
        'line_ids',
        'line_ids.product_id',
        'line_ids.quantity',
        'picking_id',
        'picking_id.state',
    )
    def _compute_receipt_sync_state(self):
        for job in self:
            has_unmatched_lines = any(
                self._safe_float(line.quantity, default=0.0) > 0 and not line.product_id
                for line in job.line_ids
            )
            if not job.line_ids:
                job.receipt_sync_state = 'not_ready'
            elif not job.picking_id:
                job.receipt_sync_state = 'not_synced'
            elif has_unmatched_lines:
                job.receipt_sync_state = 'needs_review'
            elif job.picking_id.state == 'done':
                job.receipt_sync_state = 'synced'
            elif job.picking_id.state == 'cancel':
                job.receipt_sync_state = 'cancelled'
            else:
                job.receipt_sync_state = 'in_progress'

    @api.model
    def _default_ai_model(self):
        return (
            os.getenv('OPENAI_INVOICE_MODEL')
            or os.getenv('OPENAI_MODEL')
            or self.env['ir.config_parameter'].sudo().get_param('automotive.openai_model')
            or 'gpt-4o-mini'
        )

    @api.model
    def _get_openai_api_key(self):
        return (
            os.getenv('OPENAI_API_KEY')
            or self.env['ir.config_parameter'].sudo().get_param('automotive.openai_api_key')
        )

    @api.model
    def _normalize_invoice_number(self, invoice_number):
        return ' '.join((invoice_number or '').split())

    @api.model
    def _normalize_invoice_number_key(self, invoice_number):
        return re.sub(r'[^A-Z0-9]+', '', self._normalize_invoice_number(invoice_number).upper())

    @api.model
    def _find_duplicate_job(
        self,
        source,
        external_id=None,
        partner_id=None,
        invoice_number=None,
        invoice_date=None,
        amount_total=None,
        document_type=None,
    ):
        if external_id:
            existing = self.search([('external_id', '=', external_id)], order='id desc', limit=1)
            if existing:
                return existing

        normalized_invoice = self._normalize_invoice_number(invoice_number)
        normalized_key = self._normalize_invoice_number_key(invoice_number)
        if partner_id and normalized_invoice:
            base_domain = [
                ('partner_id', '=', partner_id),
                '|',
                ('invoice_number', '=', normalized_invoice),
                ('invoice_number', '=ilike', normalized_invoice),
            ]
            if normalized_key:
                base_domain = [
                    ('partner_id', '=', partner_id),
                    '|',
                    '|',
                    ('invoice_number', '=', normalized_invoice),
                    ('invoice_number', '=ilike', normalized_invoice),
                    ('invoice_number', '=ilike', normalized_key),
                ]
            candidate_domains = [base_domain]
            if invoice_date:
                candidate_domains.append(base_domain + [('invoice_date', '=', invoice_date)])
            if amount_total not in (None, False):
                candidate_domains.append(base_domain + [('amount_total', '=', amount_total)])
            if invoice_date and amount_total not in (None, False):
                candidate_domains.append(base_domain + [('invoice_date', '=', invoice_date), ('amount_total', '=', amount_total)])
            if document_type:
                candidate_domains = [domain + [('document_type', '=', document_type)] for domain in candidate_domains]
            for domain in candidate_domains:
                existing = self.search(domain, order='id desc', limit=1)
                if existing:
                    return existing

        return self.browse()

    @api.model
    def upsert_invoice_job(
        self,
        *,
        source='manual',
        external_id=None,
        partner_id=None,
        invoice_number=None,
        invoice_date=None,
        amount_total=None,
        currency_id=None,
        picking_id=None,
        attachment_id=None,
        document_type=None,
        payload=None,
        batch_uid=None,
        batch_name=None,
        batch_index=None,
        batch_total=None,
    ):
        existing = self._find_duplicate_job(
            source=source,
            external_id=external_id,
            partner_id=partner_id,
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            amount_total=amount_total,
            document_type=document_type,
        )
        if existing:
            vals = {}
            if existing.state == 'failed':
                vals['state'] = 'pending'
            if not existing.partner_id and partner_id:
                vals['partner_id'] = partner_id
            if not existing.invoice_number and invoice_number:
                vals['invoice_number'] = self._normalize_invoice_number(invoice_number)
            if not existing.invoice_date and invoice_date:
                vals['invoice_date'] = invoice_date
            if (not existing.amount_total) and amount_total:
                vals['amount_total'] = amount_total
            if not existing.currency_id and currency_id:
                vals['currency_id'] = currency_id
            if not existing.picking_id and picking_id:
                vals['picking_id'] = picking_id
            if not existing.attachment_id and attachment_id:
                vals['attachment_id'] = attachment_id
            if not existing.document_type and document_type:
                vals['document_type'] = document_type
            if batch_uid:
                vals['batch_uid'] = batch_uid
            if batch_name:
                vals['batch_name'] = batch_name
            if batch_index not in (None, False):
                vals['batch_index'] = batch_index
            if batch_total not in (None, False):
                vals['batch_total'] = batch_total
            if existing.state in {'pending', 'failed'} and not existing.queued_at:
                vals['queued_at'] = fields.Datetime.now()
            if vals:
                existing.write(vals)
            if payload:
                existing._set_payload(payload)
            return existing, False

        vals = {
            'name': f'{(source or "manual").upper()} - {invoice_number or external_id or fields.Datetime.now()}',
            'source': source or 'manual',
            'state': 'pending',
            'external_id': external_id,
            'partner_id': partner_id,
            'invoice_number': self._normalize_invoice_number(invoice_number),
            'invoice_date': invoice_date,
            'amount_total': amount_total or 0.0,
            'currency_id': currency_id or self.env.company.currency_id.id,
            'picking_id': picking_id,
            'attachment_id': attachment_id,
            'document_type': document_type,
            'batch_uid': batch_uid,
            'batch_name': batch_name,
            'batch_index': batch_index or 0,
            'batch_total': batch_total or 0,
            'queued_at': fields.Datetime.now() if source in {'ocr', 'anaf'} or batch_uid else False,
        }
        job = self.create(vals)
        if payload:
            job._set_payload(payload)
        return job, True

    def _set_payload(self, payload):
        self.ensure_one()
        if payload is None or payload is False:
            self.payload_json = False
            return
        if isinstance(payload, str):
            self.payload_json = payload
            return
        self.payload_json = json.dumps(payload, ensure_ascii=False, default=str)

    def action_mark_needs_review(self):
        for job in self:
            previous_state = job.state
            job.write({'state': 'needs_review'})
            job._audit_log(
                action='custom',
                description=f'Invoice ingest manually marked for review: {job.display_name}',
                old_values={'state': previous_state},
                new_values=job._audit_job_summary(),
            )

    def _get_payload_dict(self):
        self.ensure_one()
        if not self.payload_json:
            return {}
        try:
            value = json.loads(self.payload_json)
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _set_payload_dict(self, payload):
        self.ensure_one()
        self._set_payload(payload)

    def _get_normalized_invoice_payload(self):
        self.ensure_one()
        payload = self._get_payload_dict()
        return (
            (payload.get('openai') or {}).get('normalized')
            or payload.get('normalized')
            or {}
        )

    def _get_duplicate_of_job_id(self):
        self.ensure_one()
        payload = self._get_payload_dict()
        duplicate_of = payload.get('duplicate_of')
        if duplicate_of:
            return duplicate_of

        openai_payload = payload.get('openai') or {}
        duplicate_of = openai_payload.get('duplicate_of')
        if duplicate_of:
            return duplicate_of

        normalized = openai_payload.get('normalized') or {}
        return normalized.get('duplicate_of')

    def _normalize_payload_line(self, line, supplier=None, default_vat_rate=0.0):
        self.ensure_one()
        if not isinstance(line, dict):
            return False

        raw_code = (
            line.get('product_code_raw')
            or line.get('product_code')
            or line.get('description')
            or line.get('product_description')
            or ''
        ).strip()
        description = (line.get('product_description') or line.get('description') or '').strip()
        parsed_identity = self._parse_invoice_line_identity(
            raw_code,
            product_description=description,
            supplier_hint=(line.get('supplier_brand') or '').strip(),
        )
        parsed_code = parsed_identity.get('product_code_primary') or (line.get('product_code') or '').strip()
        parsed_supplier_brand = parsed_identity.get('supplier_brand') or (line.get('supplier_brand') or '').strip()
        product, match_meta = self._match_product_with_meta(
            parsed_code,
            supplier=supplier,
            product_description=description,
            supplier_brand=parsed_supplier_brand,
            extra_codes=parsed_identity.get('code_candidates') or [],
        )
        if not product and parsed_code:
            progressive_candidates = self._progressive_tail_trim_candidates(parsed_code)
            if progressive_candidates:
                parsed_code = progressive_candidates[-1]

        matched_product_id = (
            product.id
            if product and match_meta.get('confidence', 0.0) >= AUTO_MATCH_CONFIDENCE_THRESHOLD
            else False
        )
        supplier_brand_id = False
        if matched_product_id:
            canonical_brand, canonical_supplier_id = self._brand_from_matched_product(product)
            if canonical_brand:
                parsed_supplier_brand = canonical_brand
            supplier_brand_id = canonical_supplier_id or False

        quantity = self._safe_float(
            line.get('quantity') or line.get('invoiced_quantity') or line.get('credited_quantity'),
            default=1.0,
        ) or 1.0
        unit_price = self._safe_float(
            line.get('unit_price')
            or line.get('price_unit')
            or line.get('price'),
            default=0.0,
        )
        line_total = self._safe_float(line.get('line_total'), default=0.0)
        if not unit_price and line_total and quantity:
            unit_price = line_total / quantity

        return {
            'quantity': quantity,
            'product_code_raw': raw_code,
            'product_code': parsed_code or False,
            'supplier_brand': parsed_supplier_brand,
            'supplier_brand_id': supplier_brand_id,
            'product_description': description,
            'unit_price': unit_price,
            'vat_rate': self._safe_float(line.get('vat_rate'), default=default_vat_rate or self.vat_rate or 0.0),
            'matched_product_id': matched_product_id,
            'matched_product_name': product.display_name if product else False,
            'match_status': 'matched' if matched_product_id else 'not_found',
            'match_method': match_meta.get('method'),
            'match_confidence': match_meta.get('confidence', 0.0),
        }

    @api.model
    def _infer_document_move_type_from_xml(self, xml_payload):
        if not xml_payload:
            return False
        try:
            root = ElementTree.fromstring(xml_payload.encode('utf-8') if isinstance(xml_payload, str) else xml_payload)
        except Exception:
            return False
        local_name = root.tag.rsplit('}', 1)[-1]
        if local_name == 'CreditNote':
            return 'in_refund'
        if local_name == 'Invoice':
            return 'in_invoice'
        return False

    @api.model
    def _looks_like_supplier_credit_note_text(self, text):
        haystack = self._normalize_code_value(text or '').upper()
        if not haystack:
            return False
        tokens = (
            'CREDIT NOTE',
            'CREDITNOTE',
            'NOTA DE CREDITARE',
            'NOTA CREDITARE',
            'FACTURA STORNO',
            'STORNO',
            'REFUND',
            'RETUR',
        )
        return any(token in haystack for token in tokens)

    def _infer_vendor_bill_move_type(self, payload=None, text_hint=None):
        self.ensure_one()
        payload = payload if isinstance(payload, dict) else self._get_payload_dict()

        normalized = (payload.get('openai') or {}).get('normalized') or {}
        document_type = (normalized.get('document_type') or normalized.get('invoice_type') or '').strip().lower()
        if document_type in {'creditnote', 'credit_note', 'refund', 'supplier_credit_note', 'supplier_refund'}:
            return 'in_refund'
        if document_type in {'invoice', 'bill', 'supplier_invoice'}:
            return 'in_invoice'

        if self.document_type == 'credit_note':
            return 'in_refund'
        if self.document_type == 'invoice':
            return 'in_invoice'

        raw_openai = (payload.get('openai') or {}).get('raw') or {}
        if isinstance(raw_openai, dict):
            document_type = (
                raw_openai.get('document_type')
                or raw_openai.get('invoice_type')
                or raw_openai.get('invoiceTypeCode')
                or ''
            )
            if isinstance(document_type, str):
                normalized_type = document_type.strip().lower()
                if normalized_type in {'creditnote', 'credit_note', 'refund'}:
                    return 'in_refund'
                if normalized_type in {'invoice', 'bill'}:
                    return 'in_invoice'

        raw_payload = payload.get('raw')
        if isinstance(raw_payload, dict):
            xml_payload = (
                raw_payload.get('xml')
                or raw_payload.get('ubl_xml')
                or raw_payload.get('document_xml')
                or raw_payload.get('parsed', {}).get('xml_payload')
                or raw_payload.get('parsed', {}).get('xml')
            )
            inferred = self._infer_document_move_type_from_xml(xml_payload)
            if inferred:
                return inferred

        if text_hint and self._looks_like_supplier_credit_note_text(text_hint):
            return 'in_refund'

        # OCR fallback: inspect the extracted text cached in the payload if available.
        if self._looks_like_supplier_credit_note_text(json.dumps(payload, ensure_ascii=False, default=str)):
            return 'in_refund'

        return 'in_invoice'

    @api.model
    def _detect_attachment_kind(self, binary, filename=None, mimetype=None):
        name = (filename or '').strip().lower()
        mime = (mimetype or '').strip().lower()
        if not binary:
            return ''
        if 'pdf' in mime or name.endswith('.pdf') or binary[:4] == b'%PDF':
            return 'pdf'
        if mime.startswith('image/') or name.endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff')):
            return 'image'
        try:
            from PIL import Image
            with Image.open(BytesIO(binary)) as image:
                image.verify()
            return 'image'
        except Exception:
            return ''

    @api.model
    def _prepare_ocr_image_path(self, image):
        try:
            from PIL import Image, ImageOps
        except Exception:
            return ''
        try:
            if image.mode == 'P':
                image = image.convert('RGBA')
            if image.mode in {'RGBA', 'LA'}:
                background = Image.new('RGBA', image.size, 'white')
                background.paste(image, mask=image.getchannel('A'))
                image = background.convert('RGB')
            else:
                image = image.convert('RGB')
            if max(image.size or (0, 0)) < 2400:
                image = image.resize((max(image.width * 2, 1), max(image.height * 2, 1)), Image.LANCZOS)
            image = ImageOps.grayscale(image)
            image = ImageOps.autocontrast(image)
            tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            tmp.close()
            image.save(tmp.name, format='PNG')
            return tmp.name
        except Exception:
            return ''

    @api.model
    def _ocr_image_path(self, image_path):
        if not image_path or not shutil.which('tesseract'):
            return ''
        try:
            result = subprocess.run(
                ['tesseract', image_path, 'stdout', '--psm', '6', '--dpi', '300'],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except Exception:
            return ''
        if result.returncode != 0:
            return ''
        return (result.stdout or '').strip()

    @api.model
    def _extract_image_text_with_ocr(self, binary):
        if not binary:
            return ''
        try:
            from PIL import Image, ImageSequence
        except Exception:
            return ''
        if not shutil.which('tesseract'):
            return ''

        temp_paths = []
        texts = []
        try:
            with Image.open(BytesIO(binary)) as image:
                frame_count = getattr(image, 'n_frames', 1) or 1
                frames = ImageSequence.Iterator(image) if frame_count > 1 else [image]
                for frame in frames:
                    processed_path = self._prepare_ocr_image_path(frame.copy())
                    if not processed_path:
                        continue
                    temp_paths.append(processed_path)
                    ocr_text = self._ocr_image_path(processed_path)
                    if ocr_text:
                        texts.append(ocr_text)
        except Exception:
            return ''
        finally:
            for path in temp_paths:
                try:
                    os.unlink(path)
                except Exception:
                    pass
        return '\n'.join(texts).strip()

    @api.model
    def _extract_pdf_text_with_ocr(self, binary):
        if not binary or not shutil.which('pdftoppm') or not shutil.which('tesseract'):
            return ''
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                pdf_path = os.path.join(temp_dir, 'invoice.pdf')
                prefix = os.path.join(temp_dir, 'page')
                with open(pdf_path, 'wb') as handle:
                    handle.write(binary)
                result = subprocess.run(
                    ['pdftoppm', '-png', '-r', '300', pdf_path, prefix],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                if result.returncode != 0:
                    return ''
                texts = []
                for image_path in sorted(glob.glob(f'{prefix}-*.png')):
                    ocr_text = self._ocr_image_path(image_path)
                    if ocr_text:
                        texts.append(ocr_text)
                return '\n'.join(texts).strip()
        except Exception:
            return ''

    def _extract_pdf_text(self):
        self.ensure_one()
        if not self.attachment_id or not self.attachment_id.datas:
            raise UserError('Attach a PDF or image first.')

        binary = base64.b64decode(self.attachment_id.datas)
        kind = self._detect_attachment_kind(
            binary,
            filename=self.attachment_id.name,
            mimetype=self.attachment_id.mimetype,
        )
        if kind == 'image':
            return self._extract_image_text_with_ocr(binary)
        if kind != 'pdf':
            raise UserError('Unsupported attachment type. Upload a PDF or image first.')

        layout_text = self._extract_pdf_text_with_pdftotext(binary)
        if layout_text and len(layout_text) >= 20:
            return layout_text

        text = ''
        try:
            reader = PdfReader(BytesIO(binary))
            pages = []
            for page in reader.pages:
                try:
                    pages.append(page.extract_text() or '')
                except Exception:
                    continue
            text = '\n'.join(pages).strip()
        except Exception:
            text = ''
        if text and len(text) >= 20:
            return text

        ocr_text = self._extract_pdf_text_with_ocr(binary)
        if ocr_text:
            return ocr_text

        return text

    @api.model
    def _extract_pdf_text_with_pdftotext(self, binary):
        if not binary or not shutil.which('pdftotext'):
            return ''
        try:
            with tempfile.NamedTemporaryFile(suffix='.pdf') as tmp:
                tmp.write(binary)
                tmp.flush()
                result = subprocess.run(
                    ['pdftotext', '-layout', tmp.name, '-'],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
        except Exception:
            return ''
        if result.returncode != 0:
            return ''
        return (result.stdout or '').strip()

    @api.model
    def _safe_money(self, value, default=0.0):
        if value in (None, False, ''):
            return default
        raw = str(value).strip().replace(' ', '')
        if not raw:
            return default
        try:
            if ',' in raw and '.' in raw:
                if raw.rfind(',') > raw.rfind('.'):
                    raw = raw.replace('.', '').replace(',', '.')
                else:
                    raw = raw.replace(',', '')
            elif ',' in raw:
                right = raw.split(',')[-1]
                if right.isdigit() and len(right) == 2:
                    raw = raw.replace(',', '.')
                else:
                    raw = raw.replace(',', '')
            return float(raw)
        except Exception:
            return default

    @api.model
    def _extract_invoice_totals_from_text(self, text):
        if not text:
            return {}

        out = {}
        vat_match = re.search(r'Cota\s*T\.V\.A\.\s*:?\s*([0-9]+(?:[.,][0-9]+)?)\s*%', text, re.IGNORECASE)
        if vat_match:
            out['vat_rate'] = self._safe_money(vat_match.group(1), default=0.0)

        semn_matches = list(
            re.finditer(
                r'Semnaturile\s+([0-9]{1,3}(?:[.,][0-9]{3})*[.,][0-9]{2})\s+([0-9]{1,3}(?:[.,][0-9]{3})*[.,][0-9]{2})',
                text,
                re.IGNORECASE,
            )
        )
        if semn_matches:
            last = semn_matches[-1]
            out['total_excl_vat'] = self._safe_money(last.group(1), default=0.0)
            out['vat_amount'] = self._safe_money(last.group(2), default=0.0)

        plata_matches = list(
            re.finditer(
                r'Total\s+de\s+plata[\s\S]{0,80}?([0-9]{1,3}(?:[.,][0-9]{3})*[.,][0-9]{2})',
                text,
                re.IGNORECASE,
            )
        )
        if plata_matches:
            out['amount_total'] = self._safe_money(plata_matches[-1].group(1), default=0.0)
        elif out.get('total_excl_vat') or out.get('vat_amount'):
            out['amount_total'] = (out.get('total_excl_vat') or 0.0) + (out.get('vat_amount') or 0.0)

        return out

    @api.model
    def _extract_invoice_lines_from_text(self, text, default_vat_rate=0.0):
        if not text:
            return []

        row_re = re.compile(
            r'^\s*(\d{1,3})\s+([A-Z]{2,6})\s+'
            r'([0-9]+(?:[.,][0-9]+)?)\s+'
            r'([0-9]{1,3}(?:[.,][0-9]{3})*[.,][0-9]{2})\s+'
            r'([0-9]{1,3}(?:[.,][0-9]{3})*[.,][0-9]{2})\s+'
            r'([0-9]{1,3}(?:[.,][0-9]{3})*[.,][0-9]{2})\s+'
            r'(.+?)\s*$'
        )
        footer_re = re.compile(
            r'^(Aceasta factura|Data sc:|In cazul in care plata|Orice litigiu|Semnaturile|Total\b|din care:|Expedierea s-a efectuat)',
            re.IGNORECASE,
        )

        rows = []
        current = None
        for raw_line in text.splitlines():
            line = (raw_line or '').strip()
            if not line:
                continue

            row_match = row_re.match(line)
            if row_match:
                if current:
                    rows.append(current)
                current = {
                    'sequence': int(row_match.group(1)),
                    'quantity': self._safe_money(row_match.group(3), default=1.0) or 1.0,
                    'unit_price': self._safe_money(row_match.group(4), default=0.0),
                    'line_total_excl_vat': self._safe_money(row_match.group(5), default=0.0),
                    'line_vat_amount': self._safe_money(row_match.group(6), default=0.0),
                    'desc_parts': [row_match.group(7).strip()],
                }
                continue

            if not current:
                continue
            if footer_re.match(line):
                rows.append(current)
                current = None
                continue
            if line.startswith('NC=') or line.startswith('CPV='):
                continue
            current['desc_parts'].append(line)

        if current:
            rows.append(current)

        by_sequence = {}
        for row in rows:
            seq = row.get('sequence') or 0
            if seq <= 0:
                continue
            by_sequence[seq] = row

        out = []
        for seq in sorted(by_sequence.keys()):
            row = by_sequence[seq]
            description = ' '.join(p for p in (row.get('desc_parts') or []) if p).strip()
            if not description:
                continue
            parsed = self._parse_invoice_line_identity(description)
            line_total = row.get('line_total_excl_vat') or 0.0
            vat_amount = row.get('line_vat_amount') or 0.0
            vat_rate = default_vat_rate or 0.0
            if line_total > 0 and vat_amount >= 0:
                inferred = round((vat_amount / line_total) * 100.0, 2)
                if inferred > 0:
                    vat_rate = inferred
            out.append({
                'quantity': row.get('quantity') or 1.0,
                'product_code_raw': parsed.get('product_code_raw') or description,
                'product_code': parsed.get('product_code_primary') or False,
                'supplier_brand': parsed.get('supplier_brand') or '',
                'product_description': description,
                'unit_price': row.get('unit_price') or 0.0,
                'vat_rate': vat_rate,
            })
        return out

    @api.model
    def _safe_float(self, value, default=0.0):
        if value in (None, False, ''):
            return default
        try:
            return float(str(value).replace(',', '.'))
        except Exception:
            return default

    @api.model
    def _safe_date(self, value):
        if not value:
            return False
        if isinstance(value, datetime):
            return value.date()
        raw = str(value).strip()
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d/%m/%y', '%d.%m.%Y', '%d.%m.%y'):
            try:
                return datetime.strptime(raw, fmt).date()
            except Exception:
                continue
        try:
            return fields.Date.to_date(raw)
        except Exception:
            return False

    def _find_supplier_partner(self, supplier_name=None, supplier_code=None, supplier_vat=None):
        self.ensure_one()
        Partner = self.env['res.partner']
        if self.partner_id:
            return self.partner_id

        if supplier_vat:
            clean_vat = self._normalize_cui_digits(supplier_vat)
            if clean_vat:
                partner = (
                    Partner.search([('vat', '=', clean_vat)], limit=1)
                    or Partner.search([('vat', '=ilike', f'RO{clean_vat}')], limit=1)
                    or Partner.search([('cui', '=', clean_vat)], limit=1)
                    or Partner.search([('cui', '=ilike', f'RO{clean_vat}')], limit=1)
                )
                if partner:
                    return partner

        # Try exact code first (legacy app seems to use short codes like AD/MT/CX).
        if supplier_code:
            clean_code = supplier_code.strip()
            partner = Partner.search([('name', '=ilike', clean_code)], limit=1)
            if partner:
                return partner
            partner = Partner.search([('ref', '=ilike', clean_code)], limit=1)
            if partner:
                return partner

        if supplier_name:
            partner = Partner.search([('name', '=ilike', supplier_name.strip())], limit=1)
            if partner:
                return partner
            partner = Partner.search([('name', 'ilike', supplier_name.strip())], limit=1)
            if partner:
                return partner
        return Partner

    def _get_or_create_supplier_partner(self, supplier_name=None, supplier_code=None, supplier_vat=None):
        self.ensure_one()
        partner = self._find_supplier_partner(
            supplier_name=supplier_name,
            supplier_code=supplier_code,
            supplier_vat=supplier_vat,
        )
        if partner:
            return partner

        clean_name = (supplier_name or '').strip()
        clean_vat = self._normalize_cui_digits(supplier_vat)
        if not clean_name and not clean_vat:
            return self.env['res.partner']

        if not clean_name:
            clean_name = f'Supplier {clean_vat}'

        vals = {
            'name': clean_name,
            'company_type': 'company',
            'supplier_rank': 1,
        }
        if 'client_type' in self.env['res.partner']._fields:
            vals['client_type'] = 'company'
        if clean_vat:
            vals['vat'] = f'RO{clean_vat}'
            if 'cui' in self.env['res.partner']._fields:
                vals['cui'] = clean_vat

        partner = self.env['res.partner'].sudo().create(vals)
        _logger.info(
            "Auto-created supplier partner id=%s name=%s vat=%s for invoice ingest job id=%s",
            partner.id,
            partner.name,
            vals.get('vat'),
            self.id,
        )
        return partner

    @api.model
    def _extract_invoice_number_from_filename(self, filename):
        stem = os.path.splitext(os.path.basename(filename or ''))[0]
        compact = re.sub(r'[^A-Z0-9]', '', stem.upper())
        if compact and sum(ch.isdigit() for ch in compact) >= 4:
            return compact
        return ''

    @api.model
    def _extract_invoice_header_from_text(self, text, filename=None):
        if not text and not filename:
            return {}

        out = {}
        raw_lines = [line.rstrip() for line in (text or '').splitlines()]
        non_empty_lines = [line.strip() for line in raw_lines if line and line.strip()]

        for idx, line in enumerate(non_empty_lines):
            if re.search(r'\bFurnizor\b', line, re.IGNORECASE):
                for candidate in non_empty_lines[idx + 1:idx + 5]:
                    parts = [part.strip() for part in re.split(r'\s{2,}', candidate) if part.strip()]
                    if parts:
                        out['supplier_name'] = parts[0]
                        break
                if out.get('supplier_name'):
                    break

        for line in non_empty_lines:
            if 'C.I.F.' not in line.upper():
                continue
            parts = [part.strip() for part in re.split(r'\s{2,}', line) if part.strip()]
            vat = next((part for part in parts if re.fullmatch(r'RO?\d{2,}', part, re.IGNORECASE)), '')
            if vat:
                out['supplier_vat'] = vat
                break

        dates = re.findall(r'\b\d{2}[./-]\d{2}[./-]\d{2,4}\b', text or '')
        if dates:
            out['invoice_date'] = dates[0]
            if len(dates) > 1:
                out['invoice_due_date'] = dates[-1]

        invoice_number = self._extract_invoice_number_from_filename(filename)
        if not invoice_number:
            scan_area = ''.join(non_empty_lines[:4]).upper()
            scan_area = re.sub(r'[^A-Z0-9]', '', scan_area)
            match = re.search(r'(RO\d{6,}|[A-Z]{1,4}\d{6,})', scan_area)
            if match:
                invoice_number = match.group(1)
        if invoice_number:
            out['invoice_number'] = invoice_number

        return out

    @api.model
    def _normalize_cui_digits(self, value):
        if not value:
            return ''
        return ''.join(ch for ch in str(value) if ch.isdigit())

    def _resolve_supplier_for_billing(self):
        """Best-effort supplier resolution for bill creation."""
        self.ensure_one()
        if self.partner_id:
            return self.partner_id

        # 1) If job is linked to a reception, use its supplier.
        if self.picking_id and self.picking_id.partner_id:
            self.partner_id = self.picking_id.partner_id.id
            return self.partner_id

        payload = self._get_payload_dict()

        # 2) OpenAI normalized supplier hints.
        normalized = self._get_normalized_invoice_payload()
        supplier = self._get_or_create_supplier_partner(
            supplier_name=(normalized.get('supplier_name') or '').strip(),
            supplier_code=(normalized.get('supplier_code') or '').strip(),
            supplier_vat=(normalized.get('supplier_vat') or '').strip(),
        )
        if supplier:
            self.partner_id = supplier.id
            return self.partner_id

        # 3) ANAF parsed CUI fallback.
        parsed_payload = payload.get('parsed') or {}
        supplier_cui = self._normalize_cui_digits(parsed_payload.get('supplier_cui'))
        if supplier_cui:
            Partner = self.env['res.partner']
            supplier = (
                Partner.search([('vat', '=', supplier_cui)], limit=1)
                or Partner.search([('vat', '=ilike', f'RO{supplier_cui}')], limit=1)
                or Partner.search([('cui', '=', supplier_cui)], limit=1)
                or Partner.search([('cui', '=ilike', f'RO{supplier_cui}')], limit=1)
            )
            if supplier:
                self.partner_id = supplier.id
                return self.partner_id

        return self.env['res.partner']

    def _process_ingest_job(self, raise_on_error=False):
        for job in self:
            if job.state not in {'pending', 'failed', 'needs_review'}:
                continue
            try:
                job.write({
                    'state': 'running',
                    'error': False,
                    'started_at': fields.Datetime.now(),
                    'finished_at': False,
                })
                if job.source == 'ocr' and job.attachment_id:
                    job.action_extract_with_openai()
                elif job.account_move_id:
                    job.write({'state': 'done', 'finished_at': fields.Datetime.now()})
                else:
                    job.write({'state': 'needs_review', 'finished_at': fields.Datetime.now()})
                if job.state in {'done', 'needs_review'} and not job.finished_at:
                    job.write({'finished_at': fields.Datetime.now()})
            except Exception as exc:  # noqa: BLE001
                job.write({
                    'state': 'failed',
                    'error': str(exc) or repr(exc),
                    'finished_at': fields.Datetime.now(),
                })
                if raise_on_error:
                    raise
        return True

    @api.model
    def _normalize_code_value(self, value):
        raw = (value or '').strip().upper()
        if not raw:
            return ''
        # Normalize mixed dash characters from OCR/PDF extraction.
        raw = (
            raw.replace('–', '-')
            .replace('—', '-')
            .replace('−', '-')
        )
        return ' '.join(raw.split())

    @api.model
    def _compact_code(self, value):
        return re.sub(r'[^A-Z0-9]', '', self._normalize_code_value(value))

    @api.model
    def _is_supplier_token(self, token):
        token = self._normalize_code_value(token)
        compact = self._compact_code(token)
        if not compact:
            return False
        if len(compact) > 15:
            return False
        if compact in INVOICE_SUPPLIER_NOISE_TOKENS:
            return False
        if compact.isdigit():
            return False
        letters = sum(1 for ch in compact if ch.isalpha())
        if letters < 2:
            return False
        return True

    @api.model
    def _extract_supplier_brand(self, raw_text, supplier_hint=None):
        if supplier_hint and self._is_supplier_token(supplier_hint):
            return self._compact_code(supplier_hint)

        text = self._normalize_code_value(raw_text)
        if not text:
            return ''

        parts = [part.strip() for part in re.split(r'\s+-\s+', text) if part.strip()]
        for part in reversed(parts):
            if self._is_supplier_token(part):
                return self._compact_code(part)

        tokens = text.split()
        for token in reversed(tokens):
            if self._is_supplier_token(token):
                return self._compact_code(token)
        return ''

    @api.model
    def _extract_primary_code(self, raw_text):
        text = self._normalize_code_value(raw_text)
        if not text:
            return ''

        parts = [part.strip() for part in re.split(r'\s+-\s+', text) if part.strip()]
        if len(parts) >= 2:
            return self._extract_primary_code(parts[0])

        tokens = [tok.strip(",.;:()[]") for tok in text.split() if tok.strip(",.;:()[]")]
        if not tokens:
            return ''

        first = re.sub(r'[^A-Z0-9-]', '', self._normalize_code_value(tokens[0]))
        if not first:
            return ''

        selected = [first]
        if self._compact_code(first) in INVOICE_CODE_STOP_WORDS:
            return ''

        # Handles patterns like "VKBA 6649" / "TI 15 92"
        for token in tokens[1:4]:
            normalized = self._normalize_code_value(token)
            compact = re.sub(r'[^A-Z0-9-]', '', normalized)
            if not compact:
                break
            if self._compact_code(compact) in INVOICE_CODE_STOP_WORDS:
                break

            first_compact = self._compact_code(selected[0])
            first_is_short_alpha = first_compact.isalpha() and len(first_compact) <= 5
            if first_is_short_alpha and compact.isdigit() and len(compact) <= 4:
                selected.append(compact)
                continue
            if len(selected) >= 2 and selected[1].isdigit() and compact.isdigit() and len(compact) <= 4:
                selected.append(compact)
                continue
            break

        return ' '.join(selected)

    @api.model
    def _trimmed_code_variants(self, code):
        compact = self._compact_code(code)
        variants = []

        def _add(candidate):
            if candidate and candidate not in variants:
                variants.append(candidate)

        for suffix in INVOICE_TRIM_SUFFIXES:
            if compact.endswith(suffix) and len(compact) > len(suffix) + 3:
                _add(compact[: -len(suffix)])
        return variants

    @api.model
    def _progressive_tail_trim_candidates(self, code):
        """
        Last-resort fallback for codes where supplier/suffix is glued to the end,
        e.g. A1353DREIS -> A1353, AVX10X700CT -> AVX10X700.
        We trim one trailing letter at a time with hard limits.
        """
        compact = self._compact_code(code)
        if not compact:
            return []
        if not re.search(r'[A-Z]$', compact):
            return []
        if not re.search(r'\d', compact):
            return []

        candidates = []
        current = compact
        steps = 0
        while (
            current
            and current[-1].isalpha()
            and len(current) > PROGRESSIVE_TRIM_MIN_LEN
            and steps < PROGRESSIVE_TRIM_MAX_STEPS
        ):
            current = current[:-1]
            steps += 1
            if current and current not in candidates:
                candidates.append(current)
        return candidates

    @api.model
    def _code_candidates(self, value, extra=None):
        candidates = []

        def _add(raw_value):
            normalized = self._normalize_code_value(raw_value)
            if not normalized:
                return
            for candidate in (
                normalized,
                re.sub(r'\s*-\s*', '-', normalized),
                normalized.replace(' ', ''),
                normalized.replace('-', ''),
                re.sub(r'[^A-Z0-9]', '', normalized),
            ):
                if candidate and candidate not in candidates:
                    candidates.append(candidate)

        _add(value)
        for raw in (extra or []):
            _add(raw)
        return candidates

    @api.model
    def _parse_invoice_line_identity(self, product_code_raw, product_description='', supplier_hint=''):
        raw_code = self._normalize_code_value(product_code_raw)
        raw_description = self._normalize_code_value(product_description)

        # Drop NC/CPV metadata, these are fiscal/procurement classifications, not SKU identifiers.
        combined = f'{raw_code} {raw_description}'.strip()
        for marker in INVOICE_META_PREFIXES:
            combined = re.sub(rf'\b{marker}\s*[^\s]+', ' ', combined, flags=re.IGNORECASE)
        combined = ' '.join(combined.split())

        primary = self._extract_primary_code(raw_code or combined)
        if not primary:
            primary = self._extract_primary_code(combined)

        supplier_brand = self._extract_supplier_brand(raw_code or combined, supplier_hint=supplier_hint)

        parsed = {
            'product_code_raw': raw_code or product_code_raw or '',
            'product_code_primary': primary or '',
            'product_code_compact': self._compact_code(primary),
            'supplier_brand': supplier_brand,
            'code_candidates': [],
        }
        parsed['code_candidates'] = self._code_candidates(
            parsed['product_code_primary'],
            extra=self._trimmed_code_variants(parsed['product_code_primary']),
        )
        return parsed

    @api.model
    def _supplier_product_domain(self, supplier):
        if not supplier:
            return []
        supplier_rec = supplier
        if not isinstance(supplier_rec, models.BaseModel):
            try:
                supplier_rec = self.env['res.partner'].browse(int(supplier))
            except Exception:
                return []
        supplier_rec = supplier_rec[:1]
        if not supplier_rec or supplier_rec._name != 'res.partner':
            return []
        return ['|', ('main_supplier_id', '=', supplier_rec.id), ('product_tmpl_id.seller_ids.partner_id', '=', supplier_rec.id)]

    @api.model
    def _supplier_brand_domain(self, supplier_brand):
        brand = self._normalize_code_value(supplier_brand)
        if not brand:
            return []
        return [
            '|', '|', '|',
            ('product_tmpl_id.tecdoc_supplier_name', '=ilike', brand),
            ('product_tmpl_id.tecdoc_variant_ids.supplier_name', '=ilike', brand),
            ('main_supplier_id.name', '=ilike', brand),
            ('main_supplier_id.ref', '=ilike', brand),
        ]

    @api.model
    def _match_by_catalog_lookup(self, code, supplier_domain=None, supplier_brand_domain=None):
        Product = self.env['product.product']
        key = self._compact_code(code)
        if not key:
            return Product, ''

        lookup_domain = [
            '|', '|', '|',
            ('product_tmpl_id.tecdoc_article_no_key', '=', key),
            ('product_tmpl_id.tecdoc_variant_ids.oem_number_ids.number_key', '=', key),
            ('product_tmpl_id.tecdoc_variant_ids.ean_ids.ean_key', '=', key),
            ('product_tmpl_id.tecdoc_variant_ids.cross_link_ids.cross_number_id.number_key', '=', key),
        ]
        scoped_domains = []
        if supplier_domain:
            scoped_domains.append((supplier_domain, ' supplier'))
        if supplier_brand_domain:
            scoped_domains.append((supplier_brand_domain, ' supplier brand'))
        scoped_domains.append(([], ''))
        for extra_domain, reason_suffix in scoped_domains:
            if extra_domain:
                product = Product.search(expression.AND([lookup_domain, extra_domain]), limit=1)
            else:
                product = Product.search(lookup_domain, limit=1)
            if product:
                return product, f'lookup{reason_suffix}'
        return Product, ''

    def _match_product(self, product_code, supplier=None, product_description=None, supplier_brand=None, extra_codes=None):
        product, _meta = self._match_product_with_meta(
            product_code=product_code,
            supplier=supplier,
            product_description=product_description,
            supplier_brand=supplier_brand,
            extra_codes=extra_codes,
        )
        return product

    @api.model
    def _brand_from_matched_product(self, product):
        product = (product or self.env['product.product'])[:1]
        if not product:
            return '', False

        brand_name = (product.tecdoc_supplier_name or '').strip()
        supplier_id = False
        try:
            supplier_id = int(product.tecdoc_supplier_id or 0) or False
        except Exception:
            supplier_id = False

        variants = product.product_tmpl_id.tecdoc_variant_ids
        variant = variants[:1]
        article_no = (product.tecdoc_article_no or '').strip().upper()
        if variants and article_no:
            by_article = variants.filtered(lambda v: (v.article_no or '').strip().upper() == article_no)
            if by_article:
                variant = by_article[:1]

        if variant:
            if not brand_name:
                brand_name = (variant.supplier_name or '').strip()
            if not supplier_id:
                supplier_id = (
                    variant.supplier_external_id
                    or (variant.supplier_id.supplier_id if variant.supplier_id else False)
                    or False
                )

        if not brand_name and product.main_supplier_id:
            brand_name = (product.main_supplier_id.name or '').strip()

        return brand_name, supplier_id or False

    def _match_product_with_meta(self, product_code, supplier=None, product_description=None, supplier_brand=None, extra_codes=None):
        self.ensure_one()
        Product = self.env['product.product']
        supplier_domain = self._supplier_product_domain(supplier)
        supplier_brand_domain = self._supplier_brand_domain(supplier_brand)
        codes = self._code_candidates(product_code, extra=extra_codes)

        # 1) Strict exact matching by code fields; try constrained scopes first.
        scoped_domains = []
        if supplier_domain:
            scoped_domains.append((supplier_domain, ' supplier'))
        if supplier_brand_domain:
            scoped_domains.append((supplier_brand_domain, ' supplier brand'))
        scoped_domains.append(([], ''))

        # Prefer article-based matching first (TecDoc articleNo / internal references),
        # then fall back to supplier/barcode fields.
        for field_name in ('tecdoc_article_no', 'default_code', 'supplier_code', 'barcode_internal', 'barcode'):
            for code in codes:
                base_domain = [(field_name, '=', code)]
                for extra_domain, reason_suffix in scoped_domains:
                    if extra_domain:
                        product = Product.search(expression.AND([base_domain, extra_domain]), limit=1)
                    else:
                        product = Product.search(base_domain, limit=1)
                    if product:
                        return product, {
                            'method': f'exact:{field_name}{reason_suffix}',
                            'matched_code': code,
                            'confidence': 100.0 if reason_suffix else 96.0,
                        }

        # 2) Exact TecDoc lookup through variant/oem/ean/cross relations.
        for code in codes:
            product, lookup_reason = self._match_by_catalog_lookup(
                code=code,
                supplier_domain=supplier_domain,
                supplier_brand_domain=supplier_brand_domain,
            )
            if product:
                confidence = 95.0 if 'supplier' in lookup_reason else 84.0
                return product, {
                    'method': lookup_reason or 'lookup',
                    'matched_code': code,
                    'confidence': confidence,
                }

        # 2b) Progressive tail-trim pass (only if strict candidates failed).
        trim_candidates = self._progressive_tail_trim_candidates(product_code)
        for code in trim_candidates:
            # Exact match only for this fallback.
            for field_name in ('tecdoc_article_no', 'default_code', 'supplier_code', 'barcode_internal', 'barcode'):
                base_domain = [(field_name, '=', code)]
                for extra_domain, reason_suffix in scoped_domains:
                    if extra_domain:
                        product = Product.search(expression.AND([base_domain, extra_domain]), limit=1)
                    else:
                        product = Product.search(base_domain, limit=1)
                    if product:
                        confidence = 83.0 if reason_suffix else 75.0
                        return product, {
                            'method': f'progressive_trim:{field_name}{reason_suffix}',
                            'matched_code': code,
                            'confidence': confidence,
                        }

            product, lookup_reason = self._match_by_catalog_lookup(
                code=code,
                supplier_domain=supplier_domain,
                supplier_brand_domain=supplier_brand_domain,
            )
            if product:
                confidence = 82.0 if 'supplier' in lookup_reason else 74.0
                return product, {
                    'method': f'progressive_trim:{lookup_reason or "lookup"}',
                    'matched_code': code,
                    'confidence': confidence,
                }

        # 3) Relaxed `ilike` only for code-like fields and only when we do have a code.
        for field_name in ('tecdoc_article_no', 'default_code', 'supplier_code'):
            for code in codes:
                if len(self._compact_code(code)) < 4:
                    continue
                base_domain = [(field_name, '=ilike', code)]
                for extra_domain, reason_suffix in scoped_domains:
                    if extra_domain:
                        product = Product.search(expression.AND([base_domain, extra_domain]), limit=1)
                    else:
                        product = Product.search(base_domain, limit=1)
                    if product:
                        confidence = 86.0 if reason_suffix else 80.0
                        return product, {
                            'method': f'ilike:{field_name}{reason_suffix}',
                            'matched_code': code,
                            'confidence': confidence,
                        }

        # 4) Description fallback only when no code was parsed at all.
        description = (product_description or '').strip()
        if description and not codes:
            exact_name_domain = [('name', '=ilike', ' '.join(description.split()))]
            for extra_domain, reason_suffix in scoped_domains:
                if extra_domain:
                    product = Product.search(expression.AND([exact_name_domain, extra_domain]), limit=1)
                else:
                    product = Product.search(exact_name_domain, limit=1)
                if product:
                    confidence = 70.0 if reason_suffix else 62.0
                    return product, {
                        'method': f'description_exact{reason_suffix}',
                        'matched_code': '',
                        'confidence': confidence,
                    }

        return Product, {
            'method': 'not_found',
            'matched_code': '',
            'confidence': 0.0,
        }

    def _replace_lines_from_normalized(self, normalized_lines):
        self.ensure_one()
        commands = [(5, 0, 0)]
        sequence = 1
        for line in normalized_lines or []:
            if not isinstance(line, dict):
                continue
            raw_code = (line.get('product_code_raw') or line.get('product_code') or '').strip()
            parsed = self._parse_invoice_line_identity(
                raw_code,
                product_description=(line.get('product_description') or '').strip(),
                supplier_hint=(line.get('supplier_brand') or '').strip(),
            )
            parsed_code = parsed.get('product_code_primary') or (line.get('product_code') or '').strip()
            supplier_brand = parsed.get('supplier_brand') or (line.get('supplier_brand') or '').strip()
            try:
                supplier_brand_id = int(line.get('supplier_brand_id') or 0) or False
            except Exception:
                supplier_brand_id = False
            product_id = line.get('matched_product_id')
            match_method = (line.get('match_method') or '').strip()
            match_confidence = self._safe_float(line.get('match_confidence'))
            matched_product = self.env['product.product']
            if product_id:
                try:
                    matched_product = self.env['product.product'].browse(int(product_id)).exists()
                except Exception:
                    matched_product = self.env['product.product']
                    product_id = False
            if not product_id:
                product, match_meta = self._match_product_with_meta(
                    parsed_code,
                    supplier=self.partner_id,
                    product_description=(line.get('product_description') or '').strip(),
                    supplier_brand=supplier_brand,
                    extra_codes=parsed.get('code_candidates') or [],
                )
                if not product and parsed_code:
                    # Keep UI code cleaner if no strict match and we can isolate a canonical base.
                    progressive_candidates = self._progressive_tail_trim_candidates(parsed_code)
                    if progressive_candidates:
                        parsed_code = progressive_candidates[-1]
                if product and match_meta.get('confidence', 0.0) >= AUTO_MATCH_CONFIDENCE_THRESHOLD:
                    product_id = product.id
                    matched_product = product
                else:
                    product_id = False
                match_method = match_meta.get('method', '')
                match_confidence = match_meta.get('confidence', 0.0)
            if matched_product:
                canonical_brand, canonical_supplier_id = self._brand_from_matched_product(matched_product)
                if canonical_brand:
                    supplier_brand = canonical_brand
                supplier_brand_id = canonical_supplier_id or supplier_brand_id or False
            else:
                supplier_brand_id = False
            commands.append((0, 0, {
                'sequence': sequence,
                'quantity': self._safe_float(line.get('quantity'), default=1.0) or 1.0,
                'product_code_raw': raw_code,
                'product_code': parsed_code,
                'supplier_brand': supplier_brand,
                'supplier_brand_id': supplier_brand_id,
                'product_description': (line.get('product_description') or '').strip(),
                'unit_price': self._safe_float(line.get('unit_price'), default=0.0),
                'vat_rate': self._safe_float(line.get('vat_rate'), default=self.vat_rate or 0.0),
                'product_id': product_id or False,
                'match_method': match_method,
                'match_confidence': match_confidence,
            }))
            sequence += 1
        self.write({'line_ids': commands})

    def _get_default_incoming_picking_type(self):
        self.ensure_one()
        company = self.env.company
        PickingType = self.env['stock.picking.type']
        picking_type = PickingType.search(
            [('code', '=', 'incoming'), ('warehouse_id.company_id', '=', company.id)],
            order='sequence, id',
            limit=1,
        )
        if not picking_type:
            picking_type = PickingType.search(
                [('code', '=', 'incoming'), ('company_id', '=', company.id)],
                order='sequence, id',
                limit=1,
            )
        if not picking_type:
            raise UserError('No incoming picking type found. Configure Inventory receipts first.')
        return picking_type

    def _collect_receipt_quantities(self):
        self.ensure_one()
        quantities = defaultdict(float)
        unmatched_count = 0
        for line in self.line_ids.sorted('sequence'):
            qty = self._safe_float(line.quantity, default=0.0)
            if qty <= 0:
                continue
            if not line.product_id:
                unmatched_count += 1
                continue
            quantities[line.product_id.id] += qty
        return dict(quantities), unmatched_count

    def _ensure_receipt(self, supplier):
        self.ensure_one()
        if not supplier:
            raise UserError('A supplier is required before reception synchronization can continue.')
        if not self.invoice_number:
            raise UserError('Invoice number is required before reception synchronization can continue.')

        normalized_invoice_number = self.env['stock.picking']._normalize_supplier_invoice_reference(self.invoice_number)
        if self.picking_id and self.picking_id.exists() and self.picking_id.state != 'cancel':
            picking = self.picking_id
            vals = {}
            if not picking.partner_id:
                vals['partner_id'] = supplier.id
            if self.invoice_number and not picking.origin:
                vals['origin'] = f'Invoice {self.invoice_number}'
            if self.invoice_number and not picking.supplier_invoice_number:
                vals['supplier_invoice_number'] = self.invoice_number
            if self.invoice_date and not picking.supplier_invoice_date:
                vals['supplier_invoice_date'] = self.invoice_date
            if vals:
                picking.with_context(skip_audit_log=True).write(vals)
            return picking, False

        domain = [
            ('picking_type_code', '=', 'incoming'),
            ('partner_id', '=', supplier.id),
            ('supplier_invoice_number', '!=', False),
            ('state', '!=', 'cancel'),
        ]
        if self.invoice_date:
            domain.append(('supplier_invoice_date', '=', self.invoice_date))
        existing = self.env['stock.picking'].search(domain, order='id desc')
        existing = existing.filtered(
            lambda picking: self.env['stock.picking']._normalize_supplier_invoice_reference(picking.supplier_invoice_number)
            == normalized_invoice_number
        )[:1]
        if existing:
            self.picking_id = existing.id
            vals = {}
            if self.invoice_date and not existing.supplier_invoice_date:
                vals['supplier_invoice_date'] = self.invoice_date
            if vals:
                existing.with_context(skip_audit_log=True).write(vals)
            return existing, False

        picking_type = self._get_default_incoming_picking_type()
        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'partner_id': supplier.id,
            'origin': f'Invoice {self.invoice_number}' if self.invoice_number else self.name,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
            'supplier_invoice_number': self.invoice_number,
            'supplier_invoice_date': self.invoice_date,
        })
        self.picking_id = picking.id
        return picking, True

    def _sync_receipt_moves(self, picking, product_quantities):
        self.ensure_one()
        if picking.state in {'done', 'cancel'}:
            return 0

        Move = self.env['stock.move']
        MoveLine = self.env['stock.move.line']
        SaleOrderLine = self.env['sale.order.line']
        updated = 0
        for product_id, qty in product_quantities.items():
            if qty <= 0:
                continue
            product = self.env['product.product'].browse(product_id).exists()
            if not product:
                continue

            remaining_qty = qty
            target_lines = SaleOrderLine.search([
                ('state', '=', 'sale'),
                ('product_id', '=', product.id),
                ('order_id.auto_state', 'not in', ['cancel', 'delivered']),
                ('company_id', '=', picking.company_id.id),
            ]).sorted(lambda line: (line.order_id.date_order or fields.Datetime.now(), line.id))

            for sale_line in target_lines:
                if remaining_qty <= 0:
                    break
                line_needed_qty = max(sale_line.product_uom_qty - sale_line._get_ready_qty(), 0.0)
                line_needed_qty = sale_line.product_uom._compute_quantity(
                    line_needed_qty,
                    product.uom_id,
                    rounding_method='HALF-UP',
                )
                if line_needed_qty <= 0:
                    continue

                target_moves = sale_line._get_supply_target_moves(picking.location_dest_id)
                for target_move in target_moves:
                    if remaining_qty <= 0 or line_needed_qty <= 0:
                        break

                    existing_supply_qty = 0.0
                    for origin_move in target_move.move_orig_ids.filtered(
                        lambda move: move.state not in {'cancel', 'done'}
                        and move.product_id == product
                        and move.location_dest_id == target_move.location_id
                        and move.picking_id != picking
                    ):
                        existing_supply_qty += origin_move.product_uom._compute_quantity(
                            origin_move.product_uom_qty,
                            target_move.product_uom,
                            rounding_method='HALF-UP',
                        )

                    reserved_qty = target_move.quantity
                    covered_qty = reserved_qty + existing_supply_qty
                    needed_qty = max(target_move.product_uom_qty - covered_qty, 0.0)
                    needed_qty = target_move.product_uom._compute_quantity(
                        needed_qty,
                        product.uom_id,
                        rounding_method='HALF-UP',
                    )
                    if needed_qty <= 0:
                        continue

                    allocated_qty = min(remaining_qty, line_needed_qty, needed_qty)
                    linked_sale_lines = target_move._get_sale_order_lines()
                    move = picking.move_ids_without_package.filtered(
                        lambda move: move.product_id == product
                        and move.state not in {'done', 'cancel'}
                        and target_move in move.move_dest_ids
                    )[:1]
                    move_vals = {
                        'product_uom_qty': allocated_qty,
                        'quantity': allocated_qty,
                        'product_uom': product.uom_id.id,
                        'move_dest_ids': [(6, 0, [target_move.id])],
                    }
                    if len(linked_sale_lines) == 1:
                        move_vals['sale_line_id'] = linked_sale_lines.id
                        move_vals['group_id'] = linked_sale_lines.order_id.procurement_group_id.id
                    if move:
                        move.write(move_vals)
                    else:
                        move_vals.update({
                            'name': product.display_name,
                            'product_id': product.id,
                            'picking_id': picking.id,
                            'location_id': picking.location_id.id,
                            'location_dest_id': picking.location_dest_id.id,
                        })
                        move = Move.create(move_vals)
                    if move.state == 'draft':
                        move._action_confirm()

                    move_line = move.move_line_ids.filtered(
                        lambda line: line.product_id.id == product.id
                        and line.location_id.id == picking.location_id.id
                        and line.location_dest_id.id == picking.location_dest_id.id
                        and not line.lot_id
                    )[:1]
                    if move_line:
                        move_line.write({
                            'product_uom_id': product.uom_id.id,
                            'quantity': allocated_qty,
                        })
                        extra_lines = (move.move_line_ids - move_line).filtered(
                            lambda line: line.product_id.id == product.id and not line.lot_id and line.state != 'done'
                        )
                        if extra_lines:
                            extra_lines.unlink()
                    else:
                        MoveLine.create({
                            'picking_id': picking.id,
                            'move_id': move.id,
                            'product_id': product.id,
                            'product_uom_id': product.uom_id.id,
                            'location_id': picking.location_id.id,
                            'location_dest_id': picking.location_dest_id.id,
                            'quantity': allocated_qty,
                        })
                    updated += 1
                    remaining_qty -= allocated_qty
                    line_needed_qty -= allocated_qty

            if remaining_qty > 0:
                move = picking.move_ids_without_package.filtered(
                    lambda m: m.product_id.id == product.id
                    and m.state not in {'done', 'cancel'}
                    and not m.move_dest_ids
                )[:1]
                if move:
                    move.write({
                        'product_uom_qty': remaining_qty,
                        'quantity': remaining_qty,
                        'product_uom': product.uom_id.id,
                    })
                else:
                    move = Move.create({
                        'name': product.display_name,
                        'product_id': product.id,
                        'product_uom_qty': remaining_qty,
                        'quantity': remaining_qty,
                        'product_uom': product.uom_id.id,
                        'picking_id': picking.id,
                        'location_id': picking.location_id.id,
                        'location_dest_id': picking.location_dest_id.id,
                    })
                if move.state == 'draft':
                    move._action_confirm()

                move_line = move.move_line_ids.filtered(
                    lambda l: l.product_id.id == product.id
                    and l.location_id.id == picking.location_id.id
                    and l.location_dest_id.id == picking.location_dest_id.id
                    and not l.lot_id
                )[:1]
                if move_line:
                    move_line.write({
                        'product_uom_id': product.uom_id.id,
                        'quantity': remaining_qty,
                    })
                    extra_lines = (move.move_line_ids - move_line).filtered(
                        lambda l: l.product_id.id == product.id and not l.lot_id and l.state != 'done'
                    )
                    if extra_lines:
                        extra_lines.unlink()
                else:
                    MoveLine.create({
                        'picking_id': picking.id,
                        'move_id': move.id,
                        'product_id': product.id,
                        'product_uom_id': product.uom_id.id,
                        'location_id': picking.location_id.id,
                        'location_dest_id': picking.location_dest_id.id,
                        'quantity': remaining_qty,
                    })
                updated += 1

        affected_orders = self.env['sale.order']
        for move in picking.move_ids_without_package:
            affected_orders |= move._get_sale_order_lines().mapped('order_id')
            affected_orders |= move.sale_line_id.order_id
        if affected_orders:
            affected_orders._refresh_automotive_stock_state()

        return updated

    def _validate_receipt(self, picking):
        self.ensure_one()
        if not picking or picking.state in {'done', 'cancel'}:
            return bool(picking and picking.state == 'done')

        if picking.state == 'draft':
            picking.action_confirm()
        result = picking.button_validate()
        if isinstance(result, dict) and result.get('res_model') == 'stock.backorder.confirmation' and result.get('res_id'):
            self.env['stock.backorder.confirmation'].browse(result['res_id']).process()
        return picking.state == 'done'

    def _auto_create_or_update_receipt(self, supplier):
        self.ensure_one()
        if self._infer_vendor_bill_move_type() == 'in_refund':
            return {
                'created': False,
                'updated_lines': 0,
                'validated': False,
                'unmatched_count': 0,
                'reason': 'credit_note',
            }
        product_quantities, unmatched_count = self._collect_receipt_quantities()
        if not product_quantities:
            return {
                'created': False,
                'updated_lines': 0,
                'validated': False,
                'unmatched_count': unmatched_count,
                'reason': 'no_matched_products',
            }

        picking, created = self._ensure_receipt(supplier=supplier)
        updated_lines = self._sync_receipt_moves(picking, product_quantities)
        validated = False
        reason = ''
        if unmatched_count:
            reason = 'unmatched_lines'
        else:
            validated = self._validate_receipt(picking)
        return {
            'created': created,
            'updated_lines': updated_lines,
            'validated': validated,
            'unmatched_count': unmatched_count,
            'reason': reason,
        }

    def action_extract_with_openai(self):
        api_key = self._get_openai_api_key()
        if not api_key:
            raise UserError(
                'Missing OPENAI_API_KEY. Set env var OPENAI_API_KEY or config parameter automotive.openai_api_key.'
            )

        for job in self:
            text = job._extract_pdf_text()
            if not text or len(text) < 20:
                raise UserError(
                    'Document text extraction returned no usable text. Use ANAF XML import or install Tesseract OCR for scanned documents.'
                )
            pdf_totals = job._extract_invoice_totals_from_text(text)
            pdf_header = job._extract_invoice_header_from_text(
                text,
                filename=job.attachment_id.name if job.attachment_id else job.name,
            )

            prompt = (
                "Extract invoice data from Romanian automotive supplier invoice text. "
                "Return strict JSON with keys: "
                "supplier_name, supplier_code, invoice_number, invoice_date, invoice_due_date, "
                "invoice_currency, vat_rate, amount_total, confidence, warnings, document_type, invoice_lines. "
                "supplier_name must be the invoice issuer/vendor from the Furnizor or supplier section, never the client/customer. "
                "invoice_number must be the exact invoice number shown on the document header. "
                "document_type must be one of invoice, credit_note, refund, or unknown when the document is clearly a supplier credit note or refund. "
                "invoice_lines is an array of objects with: "
                "quantity, product_code, product_code_raw, supplier_brand, product_description, unit_price. "
                "product_code must be the main article code only (e.g. GDB1956, 56789, VKBA 6649). "
                "supplier_brand should contain only the supplier brand token (e.g. TRW, BOSCH, SKF). "
                "Exclude NC= and CPV= values from product_code. "
                "Use ISO date format YYYY-MM-DD. If unknown, use null. confidence must be 0..100."
            )
            body = {
                'model': job.ai_model or job._default_ai_model(),
                'response_format': {'type': 'json_object'},
                'messages': [
                    {'role': 'system', 'content': 'You are a strict invoice extraction engine. Output valid JSON only.'},
                    {
                        'role': 'user',
                        'content': (
                            f'{prompt}\n\n'
                            f'FILENAME: {(job.attachment_id.name if job.attachment_id else job.name) or ""}\n\n'
                            f'INVOICE_TEXT:\n{text[:120000]}'
                        ),
                    },
                ],
            }
            response = requests.post(
                'https://api.openai.com/v1/chat/completions',
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json',
                },
                json=body,
                timeout=120,
            )
            if response.status_code >= 400:
                raise UserError(f'OpenAI extraction failed: {response.text}')
            result = response.json()
            content = (
                result.get('choices', [{}])[0]
                .get('message', {})
                .get('content')
            )
            if not content:
                raise UserError('OpenAI returned empty content.')
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise UserError('OpenAI response is not a JSON object.')

            ai_lines = parsed.get('invoice_lines') or []
            fallback_lines = job._extract_invoice_lines_from_text(
                text,
                default_vat_rate=pdf_totals.get('vat_rate') or self._safe_float(parsed.get('vat_rate')),
            )
            lines = ai_lines
            warnings = parsed.get('warnings') if isinstance(parsed.get('warnings'), list) else []
            supplier_name = (parsed.get('supplier_name') or pdf_header.get('supplier_name') or '').strip()
            invoice_number = self._normalize_invoice_number(
                parsed.get('invoice_number') or pdf_header.get('invoice_number')
            )
            invoice_date_value = parsed.get('invoice_date') or pdf_header.get('invoice_date')
            invoice_due_date_value = parsed.get('invoice_due_date') or pdf_header.get('invoice_due_date')
            currency = self.env.company.currency_id
            currency_name = (parsed.get('invoice_currency') or '').strip().upper()
            if currency_name:
                currency = self.env['res.currency'].search([('name', '=', currency_name)], limit=1) or currency
            if not parsed.get('supplier_name') and supplier_name:
                warnings.append('Supplier name recovered from the PDF header.')
            if not parsed.get('invoice_number') and invoice_number:
                warnings.append(f'Invoice number recovered from the file/header: {invoice_number}.')
            if not parsed.get('invoice_date') and invoice_date_value:
                warnings.append('Invoice date recovered from the PDF header.')
            supplier = job._get_or_create_supplier_partner(
                supplier_name=supplier_name,
                supplier_code=parsed.get('supplier_code'),
                supplier_vat=pdf_header.get('supplier_vat'),
            )
            document_type = (parsed.get('document_type') or '').strip().lower()
            if not document_type and self._looks_like_supplier_credit_note_text(text):
                document_type = 'credit_note'
            if document_type in {'refund', 'credit_note', 'creditnote'}:
                warnings.append('Supplier credit note / refund detected.')

            duplicate = job._find_duplicate_job(
                source=job.source,
                partner_id=supplier.id if supplier else False,
                invoice_number=invoice_number,
                invoice_date=self._safe_date(invoice_date_value),
                amount_total=pdf_totals.get('amount_total') or self._safe_float(parsed.get('amount_total')),
                document_type=document_type or 'invoice',
            )
            if duplicate and duplicate.id != job.id:
                job.write({
                    'state': 'needs_review',
                    'partner_id': supplier.id if supplier else False,
                    'invoice_number': invoice_number,
                    'invoice_date': self._safe_date(invoice_date_value),
                    'amount_total': pdf_totals.get('amount_total') or self._safe_float(parsed.get('amount_total')),
                    'vat_rate': pdf_totals.get('vat_rate') or self._safe_float(parsed.get('vat_rate')),
                    'currency_id': currency.id,
                    'ai_confidence': self._safe_float(parsed.get('confidence')),
                    'error': f'Duplicate supplier invoice already exists: {duplicate.display_name}',
                })
                job._set_payload_dict({
                    'openai': {
                        'model': job.ai_model or job._default_ai_model(),
                        'raw': parsed,
                        'normalized': {
                            'supplier_name': supplier_name,
                            'supplier_code': parsed.get('supplier_code'),
                            'supplier_vat': pdf_header.get('supplier_vat'),
                            'invoice_number': invoice_number,
                            'invoice_date': invoice_date_value,
                            'invoice_due_date': invoice_due_date_value,
                            'invoice_currency': currency_name or currency.name,
                            'vat_rate': pdf_totals.get('vat_rate') or self._safe_float(parsed.get('vat_rate')),
                            'amount_total': pdf_totals.get('amount_total') or self._safe_float(parsed.get('amount_total')),
                            'confidence': self._safe_float(parsed.get('confidence')),
                            'warnings': warnings,
                            'document_type': document_type or 'invoice',
                            'invoice_lines': [],
                        },
                        'pdf_header': pdf_header,
                        'pdf_reconciliation': {
                            'total_excl_vat': pdf_totals.get('total_excl_vat'),
                            'vat_amount': pdf_totals.get('vat_amount'),
                            'amount_total': pdf_totals.get('amount_total'),
                            'fallback_line_count': len(fallback_lines),
                            'ai_line_count': len(ai_lines),
                        },
                        'duplicate_of': duplicate.id,
                    }
                })
                job._audit_log(
                    action='custom',
                    description=f'Invoice OCR extraction flagged as duplicate: {job.display_name}',
                    new_values={
                        'duplicate_of_job_id': duplicate.id,
                        'duplicate_of_name': duplicate.display_name,
                        'ai_model': job.ai_model or job._default_ai_model(),
                        'partner_id': supplier.id if supplier else False,
                        'invoice_number': invoice_number,
                        'invoice_date': self._safe_date(invoice_date_value),
                        'amount_total': pdf_totals.get('amount_total') or self._safe_float(parsed.get('amount_total')),
                        'warnings': warnings,
                    },
                )
                return
            if len(fallback_lines) > len(ai_lines):
                lines = fallback_lines
                warnings.append(
                    f'AI extracted {len(ai_lines)} lines; PDF parser found {len(fallback_lines)} lines. Using PDF parser lines.'
                )

            normalized_lines = []
            for line in lines:
                if not isinstance(line, dict):
                    continue
                raw_code = (line.get('product_code_raw') or line.get('product_code') or '').strip()
                description = (line.get('product_description') or '').strip()
                parsed_identity = job._parse_invoice_line_identity(
                    raw_code,
                    product_description=description,
                    supplier_hint=(line.get('supplier_brand') or '').strip(),
                )
                parsed_code = parsed_identity.get('product_code_primary') or (line.get('product_code') or '').strip()
                parsed_supplier_brand = parsed_identity.get('supplier_brand') or (line.get('supplier_brand') or '').strip()
                product, match_meta = job._match_product_with_meta(
                    parsed_code,
                    supplier=supplier,
                    product_description=description,
                    supplier_brand=parsed_supplier_brand,
                    extra_codes=parsed_identity.get('code_candidates') or [],
                )
                if not product and parsed_code:
                    progressive_candidates = job._progressive_tail_trim_candidates(parsed_code)
                    if progressive_candidates:
                        parsed_code = progressive_candidates[-1]
                matched_product_id = (
                    product.id
                    if product and match_meta.get('confidence', 0.0) >= AUTO_MATCH_CONFIDENCE_THRESHOLD
                    else False
                )
                supplier_brand_id = False
                if matched_product_id:
                    canonical_brand, canonical_supplier_id = job._brand_from_matched_product(product)
                    if canonical_brand:
                        parsed_supplier_brand = canonical_brand
                    supplier_brand_id = canonical_supplier_id or False
                normalized_lines.append({
                    'quantity': self._safe_float(line.get('quantity'), default=1.0) or 1.0,
                    'product_code_raw': raw_code,
                    'product_code': parsed_code or False,
                    'supplier_brand': parsed_supplier_brand,
                    'supplier_brand_id': supplier_brand_id,
                    'product_description': description,
                    'unit_price': self._safe_float(line.get('unit_price'), default=0.0),
                    'matched_product_id': matched_product_id,
                    'matched_product_name': product.display_name if product else False,
                    'match_status': 'matched' if matched_product_id else 'not_found',
                    'match_method': match_meta.get('method'),
                    'match_confidence': match_meta.get('confidence'),
                })

            payload = job._get_payload_dict()
            payload['openai'] = {
                'model': job.ai_model or job._default_ai_model(),
                'raw': parsed,
                'normalized': {
                    'supplier_name': supplier_name,
                    'supplier_code': parsed.get('supplier_code'),
                    'supplier_vat': pdf_header.get('supplier_vat'),
                    'invoice_number': invoice_number,
                    'invoice_date': invoice_date_value,
                    'invoice_due_date': invoice_due_date_value,
                    'invoice_currency': currency_name or currency.name,
                    'vat_rate': pdf_totals.get('vat_rate') or self._safe_float(parsed.get('vat_rate')),
                    'amount_total': pdf_totals.get('amount_total') or self._safe_float(parsed.get('amount_total')),
                    'confidence': self._safe_float(parsed.get('confidence')),
                    'warnings': warnings,
                    'document_type': document_type or 'invoice',
                    'invoice_lines': normalized_lines,
                },
                'pdf_header': pdf_header,
                'pdf_reconciliation': {
                    'total_excl_vat': pdf_totals.get('total_excl_vat'),
                    'vat_amount': pdf_totals.get('vat_amount'),
                    'amount_total': pdf_totals.get('amount_total'),
                    'fallback_line_count': len(fallback_lines),
                    'ai_line_count': len(ai_lines),
                },
            }
            vals = {
                'state': 'needs_review',
                'partner_id': supplier.id if supplier else False,
                'invoice_number': invoice_number,
                'invoice_date': self._safe_date(invoice_date_value),
                'amount_total': pdf_totals.get('amount_total') or self._safe_float(parsed.get('amount_total')),
                'vat_rate': pdf_totals.get('vat_rate') or self._safe_float(parsed.get('vat_rate')),
                'currency_id': currency.id,
                'document_type': document_type or 'invoice',
                'ai_confidence': self._safe_float(parsed.get('confidence')),
                'error': False,
            }
            job.write(vals)
            job._set_payload_dict(payload)
            job._replace_lines_from_normalized(normalized_lines)
            job._audit_log(
                action='custom',
                description=f'Invoice OCR extraction completed: {job.display_name}',
                new_values={
                    'ai_model': job.ai_model or job._default_ai_model(),
                    'ai_confidence': job.ai_confidence,
                    'partner_id': supplier.id if supplier else False,
                    'document_type': job.document_type,
                    'used_pdf_fallback_lines': len(fallback_lines) > len(ai_lines),
                    'warning_count': len(warnings),
                    'warnings': warnings,
                    **job._audit_line_summary(),
                },
            )

    def action_run(self):
        eligible_jobs = self.filtered(lambda job: job.state in {'pending', 'failed', 'needs_review'})
        if not eligible_jobs:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Invoice Ingest',
                    'message': 'No queued jobs were eligible for processing.',
                    'type': 'warning',
                    'sticky': False,
                },
            }
        batch = False
        batch_name = False
        if len(eligible_jobs) > 1:
            batch_name = _('Invoice ingest batch - %s') % (eligible_jobs[0].batch_name or fields.Datetime.now())
            batch = self.env['automotive.async.batch'].sudo().create({
                'name': batch_name,
                'job_type': 'invoice_ingest',
                'company_id': self.env.company.id,
                'requested_by_id': self.env.user.id,
            })

        for job in eligible_jobs:
            previous_state = job.state
            job._enqueue_async_processing(batch=batch, batch_name=batch_name)
            job._audit_log(
                action='custom',
                description=f'Invoice ingest queued for background processing: {job.display_name}',
                old_values={'state': previous_state},
                new_values=job._audit_job_summary(),
            )

        message = (
            '1 job queued for background processing.'
            if len(eligible_jobs) == 1
            else f'{len(eligible_jobs)} jobs queued for background processing.'
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Invoice Ingest',
                'message': message,
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def action_create_draft_vendor_bill(self):
        notifications = []
        for job in self:
            duplicate_of = job._get_duplicate_of_job_id()
            if duplicate_of:
                duplicate = self.browse(duplicate_of).exists()
                job._audit_blocked_action(
                    description=f'Invoice bill creation blocked for duplicate job: {job.display_name}',
                    reason='duplicate_job',
                    old_values={
                        'duplicate_of_job_id': duplicate.id if duplicate else duplicate_of,
                        'duplicate_of_name': duplicate.display_name if duplicate else False,
                    },
                )
                raise UserError('This ingest job is flagged as a duplicate. Resolve the original invoice before creating a bill.')

            supplier = job._resolve_supplier_for_billing()
            if not supplier:
                normalized = job._get_normalized_invoice_payload()
                hinted_name = (normalized.get('supplier_name') or '').strip()
                hint = f' Extracted invoice supplier hint: {hinted_name}.' if hinted_name else ''
                raise UserError(
                    'Select the invoice supplier first (the vendor who issued the invoice, '
                    'not the per-line product brand).'
                    f'{hint}'
                )
            if not job.invoice_number:
                raise UserError('Set invoice number first.')

            payload = job._get_payload_dict()
            move_type = job._infer_vendor_bill_move_type(payload=payload)
            bill_origin = 'existing_linked'

            if job.account_move_id:
                move = job.account_move_id
                if move.move_type != move_type:
                    if move.state != 'draft':
                        raise UserError(
                            'The linked vendor bill is already posted and its type does not match the imported document.'
                        )
                    move.write({'move_type': move_type})
            else:
                existing_move = self.env['account.move'].search(
                    [
                        ('move_type', '=', move_type),
                        ('partner_id', '=', supplier.id),
                        ('ref', '=', job.invoice_number),
                        ('state', '!=', 'cancel'),
                    ],
                    order='id desc',
                    limit=1,
                )
                if existing_move:
                    move = existing_move
                    bill_origin = 'reused_existing'
                else:
                    line_vals = []
                    if job.line_ids:
                        for line in job.line_ids.sorted('sequence'):
                            description = (
                                (line.product_description or '').strip()
                                or (line.product_code or '').strip()
                                or 'Imported invoice line'
                            )
                            vals = {
                                'name': description,
                                'quantity': line.quantity or 1.0,
                                'price_unit': line.discounted_unit_price or line.unit_price or 0.0,
                            }
                            if line.product_id:
                                vals['product_id'] = line.product_id.id
                            line_vals.append((0, 0, vals))
                    else:
                        parsed_lines = job._get_normalized_invoice_payload().get('invoice_lines', [])
                        for line in parsed_lines:
                            if not isinstance(line, dict):
                                continue
                            quantity = self._safe_float(line.get('quantity'), default=1.0) or 1.0
                            unit_price = self._safe_float(line.get('unit_price'), default=0.0)
                            description = (
                                (line.get('product_description') or '').strip()
                                or (line.get('product_code') or '').strip()
                                or 'Imported invoice line'
                            )
                            vals = {
                                'name': description,
                                'quantity': quantity,
                                'price_unit': unit_price,
                            }
                            if line.get('matched_product_id'):
                                vals['product_id'] = line['matched_product_id']
                            line_vals.append((0, 0, vals))

                    if not line_vals:
                        line_vals = [
                            (0, 0, {
                                'name': 'Imported invoice (needs review)',
                                'quantity': 1,
                                'price_unit': job.amount_total or 0.0,
                            }),
                        ]

                    move = self.env['account.move'].create({
                        'move_type': move_type,
                        'partner_id': supplier.id,
                        'ref': job.invoice_number,
                        'invoice_date': job.invoice_date,
                        'invoice_line_ids': line_vals,
                    })
                    bill_origin = 'created'

            job.write({'account_move_id': move.id, 'state': 'needs_review'})

            if move_type == 'in_refund':
                receipt_info = {
                    'created': False,
                    'updated_lines': 0,
                    'validated': False,
                    'reason': 'credit_note',
                }
            else:
                receipt_info = job._auto_create_or_update_receipt(supplier=supplier)

            if job.picking_id and move_type != 'in_refund':
                job.picking_id.with_context(skip_audit_log=True).write({
                    'supplier_invoice_id': move.id,
                    'supplier_invoice_number': job.invoice_number,
                    'supplier_invoice_date': job.invoice_date,
                })
            if receipt_info.get('reason') == 'credit_note':
                notifications.append(
                    f"{job.invoice_number or job.id}: credit note / refund bill created; receipt sync skipped."
                )
            elif receipt_info.get('reason') == 'no_matched_products':
                notifications.append(
                    f"{job.invoice_number or job.id}: bill created, but receipt skipped (no matched products)."
                )
            elif receipt_info.get('reason') == 'unmatched_lines':
                notifications.append(
                    f"{job.invoice_number or job.id}: bill ready; receipt has unmatched lines and was left open for review."
                )
            else:
                notifications.append(
                    f"{job.invoice_number or job.id}: bill ready; receipt {'created' if receipt_info.get('created') else 'updated'} "
                    f"({receipt_info.get('updated_lines', 0)} lines), validated={bool(receipt_info.get('validated'))}."
                )
            job._audit_log(
                action='custom',
                description=f'Invoice ingest vendor bill prepared: {job.display_name}',
                new_values={
                    'account_move_id': move.id,
                    'move_type': move.move_type,
                    'bill_origin': bill_origin,
                    'partner_id': supplier.id,
                    'picking_id': job.picking_id.id if job.picking_id else False,
                    'receipt_info': receipt_info,
                    **job._audit_line_summary(),
                },
            )

        if len(self) == 1 and notifications:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Invoice Import',
                    'message': notifications[0],
                    'type': 'success',
                    'sticky': False,
                    'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
                },
            }
        return True

    def action_sync_receipt_stock(self):
        notifications = []
        for job in self:
            duplicate_of = job._get_duplicate_of_job_id()
            if duplicate_of:
                duplicate = self.browse(duplicate_of).exists()
                job._audit_blocked_action(
                    description=f'Invoice receipt sync blocked for duplicate job: {job.display_name}',
                    reason='duplicate_job',
                    old_values={
                        'duplicate_of_job_id': duplicate.id if duplicate else duplicate_of,
                        'duplicate_of_name': duplicate.display_name if duplicate else False,
                    },
                )
                raise UserError('This ingest job is flagged as a duplicate. Resolve the original invoice before syncing receipt stock.')

            supplier = job._resolve_supplier_for_billing() or job.partner_id
            move_type = job._infer_vendor_bill_move_type()
            if move_type == 'in_refund':
                receipt_info = {
                    'created': False,
                    'updated_lines': 0,
                    'validated': False,
                    'reason': 'credit_note',
                }
            else:
                receipt_info = job._auto_create_or_update_receipt(supplier=supplier)
            if job.account_move_id and job.picking_id and move_type != 'in_refund':
                job.picking_id.with_context(skip_audit_log=True).write({
                    'supplier_invoice_id': job.account_move_id.id,
                    'supplier_invoice_number': job.invoice_number,
                    'supplier_invoice_date': job.invoice_date,
                })
            if receipt_info.get('reason') == 'credit_note':
                notifications.append(f"{job.invoice_number or job.id}: receipt sync skipped for credit note / refund.")
            elif receipt_info.get('reason') == 'no_matched_products':
                notifications.append(f"{job.invoice_number or job.id}: no matched lines, nothing received.")
            elif receipt_info.get('reason') == 'unmatched_lines':
                notifications.append(
                    f"{job.invoice_number or job.id}: receipt updated, but unmatched lines remain; validation left pending review."
                )
            else:
                notifications.append(
                    f"{job.invoice_number or job.id}: receipt {'created' if receipt_info.get('created') else 'updated'} "
                    f"({receipt_info.get('updated_lines', 0)} lines), validated={bool(receipt_info.get('validated'))}."
                )
            job._audit_log(
                action='custom',
                description=f'Invoice ingest receipt sync executed: {job.display_name}',
                new_values={
                    'partner_id': supplier.id if supplier else False,
                    'move_type': move_type,
                    'picking_id': job.picking_id.id if job.picking_id else False,
                    'account_move_id': job.account_move_id.id if job.account_move_id else False,
                    'receipt_info': receipt_info,
                    **job._audit_line_summary(),
                },
            )

        if len(self) == 1 and notifications:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Receipt Sync',
                    'message': notifications[0],
                    'type': 'success',
                    'sticky': False,
                    'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
                },
            }
        return True

    @api.model
    def cron_process_jobs(self):
        jobs = self.search(
            [
                ('state', 'in', ['pending', 'failed']),
                ('source', '=', 'ocr'),
                ('attachment_id', '!=', False),
            ],
            order='queued_at asc, id asc',
            limit=10,
        )
        queued = 0
        for job in jobs:
            with self.env.cr.savepoint():
                if job._get_async_processing_job():
                    continue
                job._enqueue_async_processing(priority=90)
                queued += 1
        return queued


class InvoiceIngestJobLine(models.Model):
    _name = 'invoice.ingest.job.line'
    _description = 'Invoice Ingest Job Line'
    _order = 'sequence, id'
    _AUDIT_FIELDS = {
        'sequence',
        'quantity',
        'product_code',
        'product_code_raw',
        'supplier_brand',
        'supplier_brand_id',
        'product_description',
        'unit_price',
        'discount_percent',
        'vat_rate',
        'markup_percent',
        'product_id',
        'match_method',
        'match_confidence',
    }

    job_id = fields.Many2one('invoice.ingest.job', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    currency_id = fields.Many2one(related='job_id.currency_id', store=True, readonly=True)
    quantity = fields.Float(default=1.0)
    product_code = fields.Char('Product Code')
    product_code_raw = fields.Char('Raw Product Code')
    # Brand/manufacturer parsed from the line text (not the invoice vendor partner).
    supplier_brand = fields.Char('Brand')
    supplier_brand_id = fields.Integer('Brand Supplier ID')
    product_description = fields.Char('Description')
    unit_price = fields.Monetary(string='PU fara TVA', currency_field='currency_id')
    discount_percent = fields.Float(string='Reducere %', default=0.0)
    discounted_unit_price = fields.Monetary(
        string='Pret unit. disc.',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    unit_price_incl_vat = fields.Monetary(
        string='PU cu TVA',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    vat_rate = fields.Float(string='TVA %')
    vat_unit_amount = fields.Monetary(
        string='TVA pe unit.',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    subtotal = fields.Monetary(
        string='Total fara TVA',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    subtotal_incl_vat = fields.Monetary(
        string='Total cu TVA',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    markup_percent = fields.Float(string='Adaos %', default=lambda self: self._default_markup_percent())
    markup_amount = fields.Monetary(
        string='Adaos',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    sale_price_excl_vat = fields.Monetary(
        string='Pret vanzare fara TVA',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    sale_price_incl_vat = fields.Monetary(
        string='Pret unit. cu TVA',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    product_id = fields.Many2one('product.product', string='Matched Product')
    matched_ean = fields.Char(related='product_id.barcode', string='EAN', readonly=True)
    matched_internal_code = fields.Char(
        related='product_id.default_code',
        string='Cod Intern',
        readonly=True,
    )
    label_display_name = fields.Char(
        string='Denumire',
        compute='_compute_label_display_fields',
    )
    label_barcode_value = fields.Char(
        string='Cod de bare',
        compute='_compute_label_display_fields',
    )
    match_method = fields.Char('Match Method')
    match_confidence = fields.Float('Match Confidence (%)')
    match_status = fields.Selection(
        [
            ('matched', 'Matched'),
            ('not_found', 'Not Found'),
            ('manual', 'Manual'),
        ],
        compute='_compute_match_status',
        store=True,
    )

    def _audit_snapshot(self, field_names=None):
        self.ensure_one()
        tracked_fields = field_names or self._AUDIT_FIELDS
        snapshot = {}
        for field_name in tracked_fields:
            if field_name not in self._fields:
                continue
            value = self[field_name]
            if isinstance(value, models.BaseModel):
                snapshot[field_name] = value.ids
            else:
                snapshot[field_name] = value
        return snapshot

    def write(self, vals):
        context = dict(self.env.context or {})
        tracked_fields = [field_name for field_name in vals.keys() if field_name in self._AUDIT_FIELDS]
        old_by_id = {}
        if tracked_fields and context.get('skip_audit_log') is not True:
            old_by_id = {line.id: line._audit_snapshot(tracked_fields) for line in self}

        result = super().write(vals)

        if tracked_fields and context.get('skip_audit_log') is not True:
            for line in self.filtered('job_id'):
                old_values = dict(old_by_id.get(line.id) or {})
                new_values = line._audit_snapshot(tracked_fields)
                line.job_id._audit_log(
                    action='custom',
                    description=(
                        f'Invoice ingest line updated: {line.job_id.display_name} / '
                        f'line {line.sequence}'
                    ),
                    old_values={
                        'line_id': line.id,
                        'sequence': old_values.get('sequence', line.sequence),
                        **old_values,
                    },
                    new_values={
                        'line_id': line.id,
                        'sequence': line.sequence,
                        **new_values,
                    },
                )

        return result

    @api.model
    def _default_markup_percent(self):
        raw = (
            os.getenv('INVOICE_INGEST_DEFAULT_MARKUP_PERCENT')
            or self.env['ir.config_parameter'].sudo().get_param('automotive.invoice_ingest_default_markup_percent')
            or '25'
        )
        try:
            return float(raw)
        except Exception:
            return 25.0

    @api.depends('quantity', 'unit_price', 'discount_percent', 'vat_rate', 'markup_percent')
    def _compute_financials(self):
        for line in self:
            quantity = line.quantity or 0.0
            unit_price = line.unit_price or 0.0
            discount_pct = line.discount_percent or 0.0
            vat_pct = line.vat_rate or 0.0
            markup_pct = line.markup_percent or 0.0

            discounted_unit = unit_price * (1 - (discount_pct / 100.0))
            vat_unit = discounted_unit * (vat_pct / 100.0)
            subtotal_excl = quantity * discounted_unit
            subtotal_incl = subtotal_excl + (quantity * vat_unit)
            markup_value = discounted_unit * (markup_pct / 100.0)
            sale_excl = discounted_unit + markup_value
            sale_incl = sale_excl * (1 + (vat_pct / 100.0))

            line.discounted_unit_price = discounted_unit
            line.unit_price_incl_vat = discounted_unit + vat_unit
            line.vat_unit_amount = vat_unit
            line.subtotal = subtotal_excl
            line.subtotal_incl_vat = subtotal_incl
            line.markup_amount = markup_value
            line.sale_price_excl_vat = sale_excl
            line.sale_price_incl_vat = sale_incl

    @api.depends('product_id', 'product_code', 'match_method', 'match_confidence')
    def _compute_match_status(self):
        for line in self:
            if line.product_id:
                method = (line.match_method or '').lower()
                confidence = line.match_confidence or 0.0
                if method.startswith('exact:') or (method.startswith('lookup') and confidence >= 90.0):
                    line.match_status = 'matched'
                else:
                    line.match_status = 'manual'
            else:
                line.match_status = 'not_found'

    @api.depends(
        'product_id',
        'product_id.display_name',
        'product_id.barcode',
        'product_id.barcode_internal',
        'product_description',
        'product_code',
        'product_code_raw',
    )
    def _compute_label_display_fields(self):
        for line in self:
            line.label_display_name = line.product_id.display_name or line.product_description or ''
            line.label_barcode_value = (
                line.product_id.barcode
                or line.product_id.barcode_internal
                or line.product_code
                or line.product_code_raw
                or line.product_id.default_code
                or ''
            )

    @api.onchange('product_code')
    def _onchange_product_code(self):
        for line in self:
            if line.product_id or not line.product_code or not line.job_id:
                continue
            parsed = line.job_id._parse_invoice_line_identity(
                line.product_code,
                product_description=line.product_description,
                supplier_hint=line.supplier_brand,
            )
            if parsed.get('product_code_primary'):
                line.product_code = parsed['product_code_primary']
            if parsed.get('supplier_brand'):
                line.supplier_brand = parsed['supplier_brand']
            if not line.product_code_raw:
                line.product_code_raw = parsed.get('product_code_raw') or line.product_code
            product, meta = line.job_id._match_product_with_meta(
                line.product_code,
                supplier=line.job_id.partner_id,
                product_description=line.product_description,
                supplier_brand=line.supplier_brand,
                extra_codes=parsed.get('code_candidates') or [],
            )
            if product:
                line.product_id = product.id
                line.match_method = meta.get('method')
                line.match_confidence = meta.get('confidence', 0.0)
                canonical_brand, canonical_supplier_id = line.job_id._brand_from_matched_product(product)
                if canonical_brand:
                    line.supplier_brand = canonical_brand
                line.supplier_brand_id = canonical_supplier_id or False

    @api.onchange('job_id')
    def _onchange_job_id_defaults(self):
        for line in self:
            if line.job_id and not line.vat_rate:
                line.vat_rate = line.job_id.vat_rate or 0.0

    @api.onchange('product_id')
    def _onchange_product_id_brand(self):
        for line in self:
            if not line.job_id or not line.product_id:
                continue
            canonical_brand, canonical_supplier_id = line.job_id._brand_from_matched_product(line.product_id)
            if canonical_brand:
                line.supplier_brand = canonical_brand
            line.supplier_brand_id = canonical_supplier_id or False

    def action_try_match(self):
        for line in self:
            if not line.job_id:
                continue
            parsed = line.job_id._parse_invoice_line_identity(
                line.product_code_raw or line.product_code,
                product_description=line.product_description,
                supplier_hint=line.supplier_brand,
            )
            parsed_code = parsed.get('product_code_primary') or line.product_code
            parsed_supplier_brand = parsed.get('supplier_brand') or line.supplier_brand
            product, meta = line.job_id._match_product_with_meta(
                parsed_code,
                supplier=line.job_id.partner_id,
                product_description=line.product_description,
                supplier_brand=parsed_supplier_brand,
                extra_codes=parsed.get('code_candidates') or [],
            )
            if not product and parsed_code:
                progressive_candidates = line.job_id._progressive_tail_trim_candidates(parsed_code)
                if progressive_candidates:
                    parsed_code = progressive_candidates[-1]
            product_id = (
                product.id
                if product and meta.get('confidence', 0.0) >= AUTO_MATCH_CONFIDENCE_THRESHOLD
                else False
            )
            canonical_brand = parsed_supplier_brand
            canonical_supplier_id = False
            if product_id:
                canonical_brand, canonical_supplier_id = line.job_id._brand_from_matched_product(product)
                canonical_brand = canonical_brand or parsed_supplier_brand
            line.with_context(skip_audit_log=True).write({
                'product_code_raw': line.product_code_raw or parsed.get('product_code_raw') or line.product_code,
                'product_code': parsed_code,
                'supplier_brand': canonical_brand,
                'supplier_brand_id': canonical_supplier_id or False,
                'product_id': product_id,
                'match_method': meta.get('method'),
                'match_confidence': meta.get('confidence', 0.0),
            })
            line.job_id._audit_log(
                action='custom',
                description=f'Invoice ingest line match attempted: {line.job_id.display_name} / line {line.sequence}',
                new_values={
                    'line_id': line.id,
                    'sequence': line.sequence,
                    'product_code': line.product_code,
                    'supplier_brand': line.supplier_brand,
                    'product_id': line.product_id.id if line.product_id else False,
                    'match_method': line.match_method,
                    'match_confidence': line.match_confidence,
                    'match_status': line.match_status,
                },
            )
        return True

    def action_clear_match(self):
        snapshots = {
            line.id: {
                'job_id': line.job_id.id if line.job_id else False,
                'sequence': line.sequence,
                'product_id': line.product_id.id if line.product_id else False,
                'supplier_brand_id': line.supplier_brand_id,
                'match_method': line.match_method,
                'match_confidence': line.match_confidence,
            }
            for line in self
        }
        self.with_context(skip_audit_log=True).write({
            'product_id': False,
            'supplier_brand_id': False,
            'match_method': False,
            'match_confidence': 0.0,
        })
        for line in self.filtered('job_id'):
            line.job_id._audit_log(
                action='custom',
                description=f'Invoice ingest line match cleared: {line.job_id.display_name} / line {line.sequence}',
                old_values=snapshots.get(line.id),
                new_values={
                    'line_id': line.id,
                    'sequence': line.sequence,
                    'product_id': False,
                    'supplier_brand_id': False,
                    'match_method': False,
                    'match_confidence': 0.0,
                    'match_status': line.match_status,
                },
            )
        return True

    def action_generate_label(self):
        self.ensure_one()
        product = self.product_id
        if product:
            label = product._prepare_label_payload(
                name=self.product_description or product.display_name,
                barcode=self.label_barcode_value,
                product_code=self.product_code or self.product_code_raw or product.supplier_code or product.default_code,
                internal_code=self.matched_internal_code or product.default_code,
                price=self.sale_price_incl_vat,
                brand=self.supplier_brand or product.tecdoc_supplier_name or product.main_supplier_id.name,
                qty=1,
            )
        else:
            label = self.env['product.product']._prepare_label_payload_from_values(
                name=self.product_description,
                barcode=self.label_barcode_value,
                product_code=self.product_code or self.product_code_raw,
                internal_code='',
                price=self.sale_price_incl_vat,
                brand=self.supplier_brand,
                qty=1,
            )
        return self.env['automotive.label.print.wizard'].open_wizard(
            labels=[label],
            source_record=self,
            label_count=max(int(ceil(self.quantity or 1.0)), 1),
            job_name=self.job_id.display_name or self.display_name,
        )

    def _prepare_label_payload(self):
        self.ensure_one()
        product_model = self.env['product.product']
        product = self.product_id
        if product:
            return product._prepare_label_payload(
                name=self.product_description or product.display_name,
                barcode=self.label_barcode_value,
                product_code=self.product_code or self.product_code_raw or product.supplier_code or product.default_code,
                internal_code=self.matched_internal_code or product.default_code,
                price=self.sale_price_incl_vat,
                brand=self.supplier_brand or product.tecdoc_supplier_name or product.main_supplier_id.name,
                qty=max(int(ceil(self.quantity or 1.0)), 1),
            )
        return product_model._prepare_label_payload_from_values(
            name=self.product_description,
            barcode=self.label_barcode_value,
            product_code=self.product_code or self.product_code_raw,
            internal_code='',
            price=self.sale_price_incl_vat,
            brand=self.supplier_brand,
            qty=max(int(ceil(self.quantity or 1.0)), 1),
        )


class InvoiceIngestUploadWizard(models.TransientModel):
    _name = 'invoice.ingest.upload.wizard'
    _description = 'Invoice Ingest Upload Wizard'

    pdf_file = fields.Binary('Document File')
    pdf_filename = fields.Char('Filename')
    upload_attachment_ids = fields.Many2many('ir.attachment', string='Documents')
    supplier_id = fields.Many2one('res.partner', string='Supplier (Optional)')

    def action_import_pdf(self):
        return self.action_import_document()

    def _collect_uploaded_documents(self):
        self.ensure_one()
        documents = []
        if self.upload_attachment_ids:
            for attachment in self.upload_attachment_ids:
                binary = attachment.datas and base64.b64decode(attachment.datas) or b''
                filename = (attachment.name or 'invoice_document').strip()
                mimetype = attachment.mimetype or mimetypes.guess_type(filename)[0] or 'application/octet-stream'
                documents.append({
                    'attachment': attachment,
                    'filename': filename,
                    'binary': binary,
                    'mimetype': mimetype,
                })
        elif self.pdf_file:
            filename = (self.pdf_filename or 'invoice_document').strip()
            binary = base64.b64decode(self.pdf_file)
            mimetype = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
            attachment = self.env['ir.attachment'].create({
                'name': filename,
                'type': 'binary',
                'datas': self.pdf_file,
                'mimetype': mimetype,
                'res_model': 'invoice.ingest.upload.wizard',
                'res_id': self.id,
            })
            documents.append({
                'attachment': attachment,
                'filename': filename,
                'binary': binary,
                'mimetype': mimetype,
            })
        return documents

    def _queue_document_job(self, document, batch_uid, batch_name, batch_index, batch_total):
        self.ensure_one()
        filename = document['filename']
        binary = document['binary']
        mimetype = document['mimetype']
        attachment = document['attachment']
        kind = self.env['invoice.ingest.job']._detect_attachment_kind(
            binary,
            filename=filename,
            mimetype=mimetype,
        )
        if kind not in {'pdf', 'image'}:
            raise UserError(f'Please upload a PDF or image file. Invalid file: {filename}')
        if kind == 'image' and not shutil.which('tesseract'):
            raise UserError('Image OCR requires Tesseract OCR to be installed on the server.')

        file_checksum = hashlib.sha256(binary).hexdigest()
        job = self.env['invoice.ingest.job'].search([
            ('source', '=', 'ocr'),
            ('external_id', '=', file_checksum),
        ], limit=1)
        reused_existing_job = bool(job)
        if not job:
            job = self.env['invoice.ingest.job'].create({
                'name': f'OCR - {filename}',
                'source': 'ocr',
                'external_id': file_checksum,
                'state': 'pending',
                'partner_id': self.supplier_id.id if self.supplier_id else False,
                'ai_model': self.env['invoice.ingest.job']._default_ai_model(),
                'batch_uid': batch_uid,
                'batch_name': batch_name,
                'batch_index': batch_index,
                'batch_total': batch_total,
                'queued_at': fields.Datetime.now(),
            })
        else:
            queue_vals = {
                'batch_uid': batch_uid,
                'batch_name': batch_name,
                'batch_index': batch_index,
                'batch_total': batch_total,
            }
            if job.state in {'failed', 'needs_review'}:
                queue_vals.update({
                    'state': 'pending',
                    'queued_at': fields.Datetime.now(),
                    'started_at': False,
                    'finished_at': False,
                    'error': False,
                })
            if not job.partner_id and self.supplier_id:
                queue_vals['partner_id'] = self.supplier_id.id
            if queue_vals:
                job.write(queue_vals)

        if not job.attachment_id:
            attachment.write({'res_model': 'invoice.ingest.job', 'res_id': job.id})
            job.write({'attachment_id': attachment.id})

        job._audit_log(
            action='custom',
            description=f'Invoice OCR import queued: {job.display_name}',
            new_values={
                'source': job.source,
                'attachment_id': job.attachment_id.id if job.attachment_id else False,
                'partner_id': job.partner_id.id if job.partner_id else False,
                'external_id': job.external_id,
                'reused_existing_job': reused_existing_job,
                'batch_uid': job.batch_uid,
                'batch_name': job.batch_name,
                'batch_index': job.batch_index,
                'batch_total': job.batch_total,
            },
        )
        return job

    def action_import_document(self):
        self.ensure_one()
        documents = self._collect_uploaded_documents()
        if not documents:
            raise UserError('Please upload at least one PDF or image first.')

        batch_uid = uuid.uuid4().hex
        batch_name = (self.pdf_filename or documents[0]['filename'] or 'invoice batch').strip()
        async_batch = self.env['automotive.async.batch'].sudo().create({
            'name': batch_name,
            'job_type': 'invoice_ingest',
            'company_id': self.env.company.id,
            'requested_by_id': self.env.user.id,
        })
        jobs = self.env['invoice.ingest.job']
        total = len(documents)
        for index, document in enumerate(documents, start=1):
            job = self._queue_document_job(
                document,
                batch_uid=batch_uid,
                batch_name=batch_name,
                batch_index=index,
                batch_total=total,
            )
            job._enqueue_async_processing(
                batch=async_batch,
                batch_uid=batch_uid,
                batch_name=batch_name,
                priority=85,
            )
            jobs |= job

        message = (
            '1 document queued for background import.'
            if len(jobs) == 1
            else f'{len(jobs)} documents queued for background import.'
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Invoice Ingest',
                'message': message,
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
