# -*- coding: utf-8 -*-
import base64
import os
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .invoice_ingest_shared import (
    INVOICE_INGEST_ASYNC_JOB_TYPE,
    INVOICE_INGEST_ASYNC_QUEUED_MESSAGE,
    INVOICE_INGEST_ASYNC_TARGET_METHOD,
)


class InvoiceIngestJobAsync(models.Model):
    _inherit = 'invoice.ingest.job'

    def _queued_async_progress_message(self):
        self.ensure_one()
        return _(INVOICE_INGEST_ASYNC_QUEUED_MESSAGE)

    def _invoice_ingest_async_domain(self, *, record_ids=None, states=None, include_source=False):
        self.ensure_one()
        domain = [
            ('job_type', '=', INVOICE_INGEST_ASYNC_JOB_TYPE),
            ('target_model', '=', self._name),
            ('target_method', '=', INVOICE_INGEST_ASYNC_TARGET_METHOD),
        ]
        if record_ids is None:
            record_ids = [self.id]
        if record_ids:
            operator = '=' if len(record_ids) == 1 else 'in'
            domain.append(('target_res_id', operator, record_ids[0] if operator == '=' else record_ids))
        if include_source:
            domain.extend([
                ('source_model', '=', self._name),
                ('source_res_id', '=', self.id),
            ])
        if states:
            domain.append(('state', 'in', list(states)))
        return domain

    def _build_queue_metadata(self, batch_uid=None, batch_name=None, batch_index=None, batch_total=None, display_state='pending'):
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
        values = self._build_queue_metadata(
            batch_uid=batch_uid,
            batch_name=batch_name,
            batch_index=batch_index,
            batch_total=batch_total,
            display_state=display_state,
        )
        self.write(values)
        return values['batch_uid']

    def _build_running_state_values(self):
        self.ensure_one()
        values = {'finished_at': False}
        if self.state != 'running':
            values['state'] = 'running'
        if self.error:
            values['error'] = False
        if not self.started_at:
            values['started_at'] = fields.Datetime.now()
        return values

    def _build_requeue_state_values(self):
        self.ensure_one()
        values = {}
        if self.state != 'pending':
            values['state'] = 'pending'
        if self.started_at:
            values['started_at'] = False
        if self.finished_at:
            values['finished_at'] = False
        if self.error:
            values['error'] = False
        return values

    def _build_failed_state_values(self, error_message):
        self.ensure_one()
        return {
            'state': 'failed',
            'error': error_message,
            'finished_at': fields.Datetime.now(),
        }

    def _build_finished_state_values(self, state):
        self.ensure_one()
        values = {}
        if self.state != state:
            values['state'] = state
        if state in {'done', 'needs_review'} and not self.finished_at:
            values['finished_at'] = fields.Datetime.now()
        return values

    @api.model
    def _create_invoice_ingest_batch(self, name):
        return self.env['automotive.async.batch'].sudo().create({
            'name': name,
            'job_type': INVOICE_INGEST_ASYNC_JOB_TYPE,
            'company_id': self.env.company.id,
            'requested_by_id': self.env.user.id,
        })

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

        self.write(self._build_queue_metadata(
            batch_uid=self.batch_uid or uuid.uuid4().hex,
            batch_name=self.batch_name or self.display_name,
            display_state='pending',
        ))
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
            self._invoice_ingest_async_domain(states=states, include_source=True),
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
        sample = self[:1]
        async_jobs = self.env['automotive.async.job'].search(
            sample._invoice_ingest_async_domain(record_ids=self.ids),
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
            values = job._build_running_state_values()
            if values:
                job.with_context(skip_audit_log=True).write(values)

    def _automotive_async_on_requeue(self, async_job):
        for job in self:
            values = job._build_requeue_state_values()
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
                    'progress_message': self._queued_async_progress_message(),
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
            INVOICE_INGEST_ASYNC_JOB_TYPE,
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
            target_method=INVOICE_INGEST_ASYNC_TARGET_METHOD,
            target_res_id=self.id,
        )
        async_job.sudo().write({
            'progress': 0.0,
            'progress_message': self._queued_async_progress_message(),
        })
        self._trigger_async_job_processor()
        return async_job

    def _get_linked_async_jobs(self, states=None):
        if not self.ids:
            return self.env['automotive.async.job']
        sample = self[:1]
        return self.env['automotive.async.job'].search(
            sample._invoice_ingest_async_domain(record_ids=self.ids, states=states),
        )

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
            batch = self._create_invoice_ingest_batch(batch_name)

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

