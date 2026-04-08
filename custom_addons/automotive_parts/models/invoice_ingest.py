# -*- coding: utf-8 -*-
import base64
import json
import logging
import os
import re
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import UserError

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
PROGRESSIVE_TRIM_SUPPLIER_TOKENS = ('AUTO TOTAL',)

_logger = logging.getLogger(__name__)


class InvoiceIngestJob(models.Model):
    _name = 'invoice.ingest.job'
    _description = 'Invoice Ingest Job'
    _order = 'queued_at desc, id desc'
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
    attachment_data = fields.Binary(
        'Invoice File Data',
        attachment=False,
        readonly=True,
    )
    attachment_filename = fields.Char('Invoice File Name', readonly=True)

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
    duplicate_of_job_id = fields.Many2one(
        'invoice.ingest.job',
        string='Duplicate Of',
        compute='_compute_duplicate_warning',
    )
    duplicate_warning_message = fields.Text(
        'Duplicate Warning Message',
        compute='_compute_duplicate_warning',
    )
    # Compatibility shim for databases that still hold an older form view arch
    # referencing this field before the module upgrade refreshes ir.ui.view.
    allow_test_duplicate_action = fields.Boolean(
        'Allow Test Duplicate Action',
        compute='_compute_allow_test_duplicate_action',
    )
    active_async_job_id = fields.Many2one(
        'automotive.async.job',
        string='Background Job',
        compute='_compute_async_progress_status',
    )
    async_progress = fields.Float(
        'Background Progress',
        compute='_compute_async_progress_status',
    )
    async_progress_percent = fields.Float(
        'Background Progress (%)',
        compute='_compute_async_progress_status',
    )
    async_progress_message = fields.Char(
        'Background Stage',
        compute='_compute_async_progress_status',
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

    @api.depends('line_ids', 'state', 'source', 'attachment_id', 'active_async_job_id', 'async_progress_message', 'async_progress_percent')
    def _compute_line_extraction_message(self):
        for job in self:
            if job.line_ids:
                job.line_extraction_message = False
            elif job.state == 'running':
                if job.async_progress_message:
                    job.line_extraction_message = _(
                        'Invoice extraction is running: %(stage)s (%(progress)s%%).'
                    ) % {
                        'stage': job.async_progress_message,
                        'progress': int(round(job.async_progress_percent or 0.0)),
                    }
                else:
                    job.line_extraction_message = _(
                        'Invoice extraction is running in the background.'
                    )
            elif job.state == 'pending':
                job.line_extraction_message = job.async_progress_message or _(
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

    @api.depends('payload_json', 'error')
    def _compute_duplicate_warning(self):
        for job in self:
            duplicate_id = job._get_duplicate_of_job_id()
            duplicate = self.browse(duplicate_id).exists() if duplicate_id else self.browse()
            if duplicate:
                job.duplicate_of_job_id = duplicate
                job.duplicate_warning_message = _(
                    'This document looks like a duplicate of "%s". Review the original import before continuing.'
                ) % duplicate.display_name
            else:
                job.duplicate_of_job_id = False
                job.duplicate_warning_message = False

    def _compute_allow_test_duplicate_action(self):
        for job in self:
            job.allow_test_duplicate_action = False

    @api.depends('state', 'queued_at', 'started_at', 'finished_at')
    def _compute_async_progress_status(self):
        jobs = self._get_display_async_jobs()
        for job in self:
            async_job = jobs.get(job.id, self.env['automotive.async.job'])
            job.active_async_job_id = async_job
            job.async_progress = async_job.progress if async_job else 0.0
            job.async_progress_percent = async_job.progress_percent if async_job else 0.0
            job.async_progress_message = async_job.progress_message if async_job else False

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

    def _queue_metadata(self, batch_uid=None, batch_name=None, batch_index=None, batch_total=None, display_state='pending'):
        self.ensure_one()
        return {
            'state': display_state or 'pending',
            'error': False,
            'queued_at': fields.Datetime.now(),
            'started_at': False,
            'finished_at': False,
            'batch_uid': batch_uid or self.batch_uid or uuid.uuid4().hex,
            'batch_name': batch_name or self.batch_name or False,
            'batch_index': batch_index if batch_index not in (None, False) else (self.batch_index or 0),
            'batch_total': batch_total if batch_total not in (None, False) else (self.batch_total or 0),
        }

    def _queue_processing(self, batch_uid=None, batch_name=None, batch_index=None, batch_total=None, display_state='pending'):
        self.ensure_one()
        values = self._queue_metadata(
            batch_uid=batch_uid,
            batch_name=batch_name,
            batch_index=batch_index,
            batch_total=batch_total,
            display_state=display_state,
        )
        self.write(values)
        return values['batch_uid']

    @api.model
    def _trigger_async_job_processor(self):
        cron = self.env.ref('automotive_parts.ir_cron_automotive_async_jobs', raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()
        return True

    def action_reprocess(self):
        self.ensure_one()
        if self.source != 'ocr' or not self.attachment_id:
            raise UserError('Only OCR imports with an attached document can be reprocessed.')

        if not self.attachment_data:
            stored_binary = self._get_attachment_binary(raise_if_missing=False)
            if stored_binary:
                self.write({
                    'attachment_data': base64.b64encode(stored_binary),
                    'attachment_filename': self.attachment_id.name,
                })

        self.write({
            'state': 'pending',
            'queued_at': fields.Datetime.now(),
            'started_at': False,
            'finished_at': False,
            'error': False,
        })
        self._enqueue_async_processing(
            batch_uid=self.batch_uid or uuid.uuid4().hex,
            batch_name=self.batch_name or self.display_name,
            priority=85,
            display_state='pending',
        )
        self._audit_log(
            action='custom',
            description=f'Invoice OCR import reprocessed: {self.display_name}',
            new_values={
                'state': self.state,
                'attachment_id': self.attachment_id.id,
                'external_id': self.external_id,
                'reprocess': True,
            },
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Invoice Ingest',
                'message': 'Existing import requeued in the background.',
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

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

    def _get_display_async_jobs(self):
        job_map = {}
        if not self.ids:
            return job_map

        priority_by_state = {
            'running': 4,
            'queued': 3,
            'failed': 2,
            'done': 1,
            'cancelled': 1,
        }
        async_jobs = self.env['automotive.async.job'].search(
            [
                ('job_type', '=', 'invoice_ingest'),
                ('target_model', '=', self._name),
                ('target_res_id', 'in', self.ids),
                ('target_method', '=', '_process_ingest_job'),
            ],
            order='id desc',
        )
        for async_job in async_jobs:
            current = job_map.get(async_job.target_res_id)
            current_priority = priority_by_state.get(current.state, 0) if current else 0
            candidate_priority = priority_by_state.get(async_job.state, 0)
            if not current or candidate_priority > current_priority:
                job_map[async_job.target_res_id] = async_job
        return job_map

    def _get_context_async_job(self):
        self.ensure_one()
        async_job_id = self.env.context.get('automotive_async_job_id')
        if async_job_id:
            async_job = self.env['automotive.async.job'].browse(async_job_id).exists()
            if async_job:
                return async_job
        return self._get_async_processing_job(states=('running', 'queued', 'done', 'failed', 'cancelled'))

    def _ensure_async_not_cancelled(self):
        self.ensure_one()
        async_job = self._get_context_async_job()
        if async_job and self.env['automotive.async.job'].is_cancel_requested(async_job.id):
            raise UserError(_('Invoice import was cancelled.'))
        return async_job

    def _report_async_progress(self, progress, message):
        self.ensure_one()
        async_job = self._ensure_async_not_cancelled()
        if not async_job:
            return False
        result = self.env['automotive.async.job'].report_progress(
            async_job.id,
            progress=progress,
            progress_message=message,
            state='running',
        )
        if not result and self.env['automotive.async.job'].is_cancel_requested(async_job.id):
            raise UserError(_('Invoice import was cancelled.'))
        return result

    def _automotive_async_on_claim(self, async_job):
        for job in self:
            values = {}
            if job.state != 'running':
                values['state'] = 'running'
            if not job.started_at:
                values['started_at'] = fields.Datetime.now()
            if job.finished_at:
                values['finished_at'] = False
            if job.error:
                values['error'] = False
            if values:
                job.with_context(skip_audit_log=True).write(values)

    def _automotive_async_on_requeue(self, async_job):
        for job in self:
            values = {}
            if job.state != 'pending':
                values['state'] = 'pending'
            if job.started_at:
                values['started_at'] = False
            if job.finished_at:
                values['finished_at'] = False
            if job.error:
                values['error'] = False
            if values:
                job.with_context(skip_audit_log=True).write(values)

    def _automotive_async_on_failed(self, async_job):
        for job in self:
            values = {}
            if job.state != 'failed':
                values['state'] = 'failed'
            if async_job.last_error and job.error != async_job.last_error:
                values['error'] = async_job.last_error
            if not job.finished_at:
                values['finished_at'] = fields.Datetime.now()
            if values:
                job.with_context(skip_audit_log=True).write(values)

    def _enqueue_async_processing(self, batch=False, batch_uid=None, batch_name=None, force=False, priority=80, display_state='pending'):
        self.ensure_one()
        existing_job = self._get_async_processing_job()
        if existing_job and not force:
            if display_state and self.state != display_state:
                self.write({'state': display_state})
            if batch and not existing_job.batch_id:
                existing_job.sudo().write({'batch_id': batch.id})
            if display_state == 'pending' and existing_job.state == 'queued':
                existing_job.sudo().write({
                    'progress': 0.0,
                    'progress_message': _('Queued, waiting for worker'),
                })
                self._trigger_async_job_processor()
            return existing_job

        effective_batch_name = batch_name or self.batch_name or self.display_name
        self._queue_processing(
            batch_uid=batch_uid,
            batch_name=effective_batch_name,
            display_state=display_state,
        )
        async_job = self.env['automotive.async.job'].enqueue_job(
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
        async_job.sudo().write({
            'progress': 0.0,
            'progress_message': _('Queued, waiting for worker'),
        })
        self._trigger_async_job_processor()
        return async_job

    def _get_linked_async_jobs(self, states=None):
        domain = [
            ('job_type', '=', 'invoice_ingest'),
            ('target_model', '=', self._name),
            ('target_res_id', 'in', self.ids),
            ('target_method', '=', '_process_ingest_job'),
        ]
        if states:
            domain.append(('state', 'in', list(states)))
        return self.env['automotive.async.job'].search(domain)

    def _cancel_linked_async_jobs(self, reason=None):
        async_jobs = self._get_linked_async_jobs(states=('queued', 'running', 'failed'))
        if not async_jobs:
            return async_jobs
        async_jobs.write({
            'state': 'cancelled',
            'finished_at': fields.Datetime.now(),
            'progress_message': reason or _('Cancelled'),
            'last_error': False,
            'last_error_type': False,
            'next_retry_at': False,
        })
        async_jobs.filtered('batch_id').mapped('batch_id')._sync_state_from_jobs()
        return async_jobs

    def unlink(self):
        self._cancel_linked_async_jobs(reason=_('Cancelled because the invoice ingest record was deleted.'))
        return super().unlink()

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

    def _sync_workflow_state(self, receipt_info=None, move_type=None):
        for job in self:
            if job.state == 'running':
                continue

            effective_move_type = move_type or job._infer_vendor_bill_move_type()
            next_state = 'needs_review'
            info = receipt_info or {}

            if effective_move_type == 'in_refund' and job.account_move_id:
                next_state = 'done'
            elif info.get('validated') and info.get('reason') not in {'no_matched_products', 'unmatched_lines'}:
                next_state = 'done'
            elif job.receipt_sync_state == 'synced':
                next_state = 'done'

            vals = {}
            if job.state != next_state:
                vals['state'] = next_state
            if next_state in {'done', 'needs_review'} and not job.finished_at:
                vals['finished_at'] = fields.Datetime.now()
            if vals:
                job.write(vals)

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
            job._enqueue_async_processing(batch=batch, batch_name=batch_name, display_state='pending')
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
        processed = self.env['automotive.async.job'].cron_process_jobs(limit=10)
        return queued + processed
