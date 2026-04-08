# -*- coding: utf-8 -*-
import json

from odoo import _, api, fields, models

from .invoice_ingest_shared import snapshot_record


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
        return snapshot_record(self, field_names or self._AUDIT_FIELDS)

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
