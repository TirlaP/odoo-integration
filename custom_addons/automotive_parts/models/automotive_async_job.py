# -*- coding: utf-8 -*-
import json
import logging
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .invoice_ingest_shared import (
    INVOICE_INGEST_ASYNC_JOB_TYPE,
    INVOICE_INGEST_ASYNC_TARGET_METHOD,
    INVOICE_INGEST_ASYNC_TARGET_MODEL,
)
from ..runtime_logging import emit_runtime_event


_logger = logging.getLogger(__name__)

def _json_dumps(value):
    return json.dumps([] if value is None else value, ensure_ascii=False, default=str)


def _json_loads(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


class AutomotiveAsyncBatch(models.Model):
    _name = 'automotive.async.batch'
    _description = 'Automotive Async Batch'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'
    _check_company_auto = True

    name = fields.Char(required=True, default=lambda self: _('Async Batch'), tracking=True)
    job_type = fields.Char(index=True, tracking=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True, tracking=True)
    requested_by_id = fields.Many2one('res.users', required=True, default=lambda self: self.env.user, tracking=True)
    user_id = fields.Many2one('res.users', related='requested_by_id', readonly=True, store=True)
    state = fields.Selection(
        [
            ('queued', 'Queued'),
            ('running', 'Running'),
            ('partial', 'Partially Done'),
            ('done', 'Done'),
            ('failed', 'Failed'),
            ('cancelled', 'Cancelled'),
        ],
        required=True,
        default='queued',
        tracking=True,
        index=True,
    )
    job_ids = fields.One2many('automotive.async.job', 'batch_id', string='Jobs')
    job_count = fields.Integer(compute='_compute_summary', string='Jobs')
    total_jobs = fields.Integer(compute='_compute_summary', string='Jobs')
    queued_job_count = fields.Integer(compute='_compute_summary')
    queued_jobs = fields.Integer(compute='_compute_summary')
    running_job_count = fields.Integer(compute='_compute_summary')
    running_jobs = fields.Integer(compute='_compute_summary')
    done_job_count = fields.Integer(compute='_compute_summary')
    done_jobs = fields.Integer(compute='_compute_summary')
    failed_job_count = fields.Integer(compute='_compute_summary')
    failed_jobs = fields.Integer(compute='_compute_summary')
    cancelled_job_count = fields.Integer(compute='_compute_summary')
    cancelled_jobs = fields.Integer(compute='_compute_summary')
    progress = fields.Float(compute='_compute_summary', string='Progress (%)')
    progress_percent = fields.Float(compute='_compute_summary', string='Progress (%)')
    started_at = fields.Datetime(tracking=True)
    finished_at = fields.Datetime(tracking=True)
    last_message = fields.Char(tracking=True)
    note = fields.Char(related='last_message', readonly=True)

    @api.model
    def _summary_from_jobs(self, jobs):
        summary = {
            'queued': 0,
            'running': 0,
            'done': 0,
            'failed': 0,
            'cancelled': 0,
            'total': 0,
            'progress_total': 0.0,
            'started_at': False,
            'finished_at': False,
            'last_message': False,
        }
        for job in jobs:
            summary['total'] += 1
            summary['progress_total'] += job.progress or 0.0
            if job.state in summary:
                summary[job.state] += 1
            if job.started_at and (not summary['started_at'] or job.started_at < summary['started_at']):
                summary['started_at'] = job.started_at
            if job.finished_at and (not summary['finished_at'] or job.finished_at > summary['finished_at']):
                summary['finished_at'] = job.finished_at
            summary['last_message'] = job.progress_message or job.last_error or summary['last_message']
        return summary

    @api.model
    def _derive_state_from_summary(self, summary):
        if not summary['total']:
            return 'queued'
        if summary['cancelled'] == summary['total']:
            return 'cancelled'
        if summary['running']:
            return 'running'
        if summary['done'] == summary['total']:
            return 'done'
        if summary['failed'] and summary['done'] + summary['failed'] == summary['total']:
            return 'failed'
        if summary['done']:
            return 'partial'
        return 'queued'

    @api.depends('job_ids.state', 'job_ids.progress')
    def _compute_summary(self):
        for batch in self:
            summary = batch._summary_from_jobs(batch.job_ids)
            total = summary['total']
            average_progress = (summary['progress_total'] / total) if total else 0.0
            batch.job_count = total
            batch.total_jobs = total
            batch.queued_job_count = summary['queued']
            batch.queued_jobs = summary['queued']
            batch.running_job_count = summary['running']
            batch.running_jobs = summary['running']
            batch.done_job_count = summary['done']
            batch.done_jobs = summary['done']
            batch.failed_job_count = summary['failed']
            batch.failed_jobs = summary['failed']
            batch.cancelled_job_count = summary['cancelled']
            batch.cancelled_jobs = summary['cancelled']
            batch.progress = average_progress
            batch.progress_percent = average_progress

    def _sync_state_from_jobs(self):
        for batch in self:
            summary = batch._summary_from_jobs(batch.job_ids)
            values = {
                'started_at': summary['started_at'] or batch.started_at or False,
                'finished_at': summary['finished_at'] if batch.state in {'done', 'failed', 'cancelled'} else False,
                'last_message': summary['last_message'] or batch.last_message,
                'state': batch._derive_state_from_summary(summary),
            }
            if values['state'] in {'done', 'failed', 'cancelled'} and not values['finished_at']:
                values['finished_at'] = fields.Datetime.now()
            batch.with_context(skip_audit_log=True).write(values)

    def action_open_jobs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Async Jobs'),
            'res_model': 'automotive.async.job',
            'view_mode': 'list,form',
            'domain': [('batch_id', '=', self.id)],
            'context': {'default_batch_id': self.id},
        }

    def action_view_jobs(self):
        return self.action_open_jobs()

    def action_cancel(self):
        for batch in self:
            batch.job_ids.filtered(lambda job: job.state in {'queued', 'failed'}).write({'state': 'cancelled'})
            batch.write({'state': 'cancelled', 'finished_at': fields.Datetime.now(), 'last_message': _('Batch cancelled.')})
        return True


class AutomotiveAsyncJob(models.Model):
    _name = 'automotive.async.job'
    _description = 'Automotive Async Job'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'priority asc, scheduled_at asc, id asc'
    _check_company_auto = True

    name = fields.Char(required=True, tracking=True)
    job_type = fields.Char(index=True, tracking=True)
    batch_id = fields.Many2one('automotive.async.batch', ondelete='set null', index=True, tracking=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True, tracking=True)
    requested_by_id = fields.Many2one('res.users', required=True, default=lambda self: self.env.user, tracking=True)
    user_id = fields.Many2one('res.users', related='requested_by_id', readonly=True, store=True)
    run_as_user_id = fields.Many2one('res.users', required=True, default=lambda self: self.env.user, tracking=True)
    source_model = fields.Char(index=True, tracking=True)
    source_res_id = fields.Integer(index=True, tracking=True)
    source_display_name = fields.Char(readonly=True, tracking=True)
    reference_model = fields.Char(related='source_model', readonly=True, store=True)
    reference_res_id = fields.Integer(related='source_res_id', readonly=True, store=True)
    target_model = fields.Char(required=True, index=True, tracking=True)
    target_res_id = fields.Integer(index=True, tracking=True)
    target_method = fields.Char(required=True, tracking=True)
    handler_model = fields.Char(related='target_model', readonly=True, store=True)
    handler_method = fields.Char(related='target_method', readonly=True, store=True)
    state = fields.Selection(
        [
            ('queued', 'Queued'),
            ('running', 'Running'),
            ('done', 'Done'),
            ('failed', 'Failed'),
            ('cancelled', 'Cancelled'),
        ],
        required=True,
        default='queued',
        index=True,
        tracking=True,
    )
    priority = fields.Integer(default=10, index=True, tracking=True)
    scheduled_at = fields.Datetime(default=fields.Datetime.now, index=True, tracking=True)
    next_retry_at = fields.Datetime(index=True, tracking=True)
    started_at = fields.Datetime(index=True, tracking=True)
    finished_at = fields.Datetime(index=True, tracking=True)
    attempt_count = fields.Integer(default=0, tracking=True)
    max_attempts = fields.Integer(default=3, tracking=True)
    progress = fields.Float(default=0.0, tracking=True)
    progress_percent = fields.Float(compute='_compute_progress_percent')
    progress_current = fields.Float(compute='_compute_progress_current')
    progress_total = fields.Float(compute='_compute_progress_total')
    progress_message = fields.Char(tracking=True)
    result_message = fields.Char(compute='_compute_result_message')
    payload_json = fields.Text(tracking=True)
    execution_context_json = fields.Text(tracking=True)
    call_args_json = fields.Text(tracking=True)
    call_kwargs_json = fields.Text(tracking=True)
    result_json = fields.Text(tracking=True)
    last_error = fields.Text(tracking=True)
    last_error_type = fields.Char(tracking=True)
    duration_seconds = fields.Float(compute='_compute_duration_seconds')

    _sql_constraints = [
        ('automotive_async_job_target_required', "CHECK(target_model <> '' AND target_method <> '')", 'Target model and method are required.'),
    ]
    _ALLOWED_TARGETS = {
        (INVOICE_INGEST_ASYNC_TARGET_MODEL, INVOICE_INGEST_ASYNC_TARGET_METHOD),
        ('invoice.ingest.job.line', '_process_tecdoc_enrichment_job'),
        ('ir.actions.report', '_run_automotive_async_label_job'),
    }

    @classmethod
    def _is_allowed_target(cls, target_model, target_method):
        return (target_model, target_method) in cls._ALLOWED_TARGETS

    def _effective_max_attempts(self):
        self.ensure_one()
        return max(int(self.max_attempts or 1), 1)

    @api.depends('started_at', 'finished_at')
    def _compute_duration_seconds(self):
        for job in self:
            if job.started_at and job.finished_at:
                job.duration_seconds = (fields.Datetime.to_datetime(job.finished_at) - fields.Datetime.to_datetime(job.started_at)).total_seconds()
            else:
                job.duration_seconds = 0.0

    @classmethod
    def _normalize_record(cls, record):
        if not record:
            return record
        if isinstance(record, models.BaseModel) and len(record) == 1:
            return record
        return record

    @staticmethod
    def _load_json(value, default):
        return _json_loads(value, default)

    @staticmethod
    def _dump_json(value):
        return _json_dumps(value)

    @api.depends('progress')
    def _compute_progress_percent(self):
        for job in self:
            job.progress_percent = max(min(job.progress or 0.0, 100.0), 0.0)

    @api.depends('progress')
    def _compute_progress_current(self):
        for job in self:
            job.progress_current = max(job.progress or 0.0, 0.0)

    @api.depends('progress')
    def _compute_progress_total(self):
        for job in self:
            job.progress_total = 100.0

    @api.depends('progress_message', 'last_error', 'state')
    def _compute_result_message(self):
        for job in self:
            if job.state == 'failed' and job.last_error:
                job.result_message = job.last_error
            elif job.progress_message:
                job.result_message = job.progress_message
            elif job.state == 'done':
                job.result_message = _('Done')
            elif job.state == 'cancelled':
                job.result_message = _('Cancelled')
            else:
                job.result_message = ''

    @api.model
    def enqueue_call(
        self,
        target_model,
        target_method,
        *,
        target_res_id=False,
        name=False,
        args=None,
        kwargs=None,
        payload=None,
        execution_context=None,
        source_record=None,
        batch=None,
        batch_name=False,
        job_type=False,
        priority=10,
        scheduled_at=False,
        company_id=False,
        requested_by_id=False,
        run_as_user_id=False,
        max_attempts=3,
    ):
        if not self._is_allowed_target(target_model, target_method):
            raise UserError(
                _('Background execution is not allowed for %(model)s.%(method)s.') % {
                    'model': target_model,
                    'method': target_method,
                }
            )
        job_model = self.sudo()
        batch_record = batch
        if batch_name and not batch_record:
            batch_record = self.env['automotive.async.batch'].sudo().create({
                'name': batch_name,
                'company_id': company_id or self.env.company.id,
                'requested_by_id': requested_by_id or self.env.user.id,
                'job_type': job_type or False,
            })
        if batch_record and isinstance(batch_record, models.BaseModel):
            batch_id = batch_record.id
            if job_type and not batch_record.job_type:
                batch_record.write({'job_type': job_type})
        else:
            batch_id = batch_record or False

        values = {
            'name': name or f'{target_model}.{target_method}',
            'job_type': job_type or False,
            'batch_id': batch_id,
            'company_id': company_id or self.env.company.id,
            'requested_by_id': requested_by_id or self.env.user.id,
            'run_as_user_id': run_as_user_id or requested_by_id or self.env.user.id,
            'target_model': target_model,
            'target_res_id': target_res_id or False,
            'target_method': target_method,
            'priority': priority,
            'scheduled_at': scheduled_at or fields.Datetime.now(),
            'max_attempts': max_attempts,
            'call_args_json': self._dump_json(args or []),
            'call_kwargs_json': self._dump_json(kwargs or {}),
            'payload_json': self._dump_json(payload or {}),
            'execution_context_json': self._dump_json(execution_context or {}),
        }
        if source_record:
            source_record = source_record.exists()
            values.update({
                'source_model': source_record._name,
                'source_res_id': source_record.id,
                'source_display_name': source_record.display_name,
            })
        return job_model.create(values)

    @api.model
    def enqueue_job(self, job_type, name=False, payload=None, args=None, kwargs=None, execution_context=None, source=False, batch=False, batch_name=False, priority=10, scheduled_at=False, company=False, requested_by=False, run_as_user=False, max_attempts=3, target_model=False, target_method=False, target_res_id=False):
        payload = dict(payload or {})
        source_record = source.exists() if isinstance(source, models.BaseModel) else False
        if job_type == INVOICE_INGEST_ASYNC_JOB_TYPE:
            target_model = target_model or INVOICE_INGEST_ASYNC_TARGET_MODEL
            target_method = target_method or INVOICE_INGEST_ASYNC_TARGET_METHOD
            target_res_id = target_res_id or payload.get('invoice_ingest_job_id')
            if source_record and not name:
                name = _('Process %s') % source_record.display_name
        elif source_record and not target_model and not target_method:
            target_model = source_record._name
            target_method = target_method or ''
            target_res_id = target_res_id or source_record.id

        if not target_model or not target_method:
            raise UserError(_('Target model and method are required to enqueue a background job.'))

        return self.enqueue_call(
            target_model,
            target_method,
            target_res_id=target_res_id,
            name=name,
            args=args,
            kwargs=kwargs,
            payload=payload,
            execution_context=execution_context,
            source_record=source_record,
            batch=batch,
            batch_name=batch_name,
            job_type=job_type,
            priority=priority,
            scheduled_at=scheduled_at,
            company_id=getattr(company, 'id', company) or False,
            requested_by_id=getattr(requested_by, 'id', requested_by) or False,
            run_as_user_id=getattr(run_as_user, 'id', run_as_user) or False,
            max_attempts=max_attempts,
        )

    @api.model
    def enqueue_record_call(self, record, method_name, **kwargs):
        record = record.exists()
        return self.enqueue_call(
            record._name,
            method_name,
            target_res_id=record.id,
            source_record=record,
            **kwargs,
        )

    @api.model
    def enqueue_batch(self, name, calls, **kwargs):
        batch = self.env['automotive.async.batch'].sudo().create({
            'name': name,
            'company_id': kwargs.pop('company_id', False) or self.env.company.id,
            'requested_by_id': kwargs.pop('requested_by_id', False) or self.env.user.id,
        })
        for call in calls:
            call = dict(call)
            call.setdefault('batch', batch)
            self.enqueue_call(**call)
        return batch

    def _get_execution_user(self):
        self.ensure_one()
        return self.run_as_user_id or self.requested_by_id or self.env.user

    def _get_target_recordset(self):
        self.ensure_one()
        target = self.env[self.target_model]
        if self.target_res_id:
            target = target.browse(self.target_res_id).exists()
        return target

    def _execute_target_call(self):
        self.ensure_one()
        if not self._is_allowed_target(self.target_model, self.target_method):
            raise UserError(
                _('Background execution is blocked for %(model)s.%(method)s.') % {
                    'model': self.target_model,
                    'method': self.target_method,
                }
            )
        target = self._get_target_recordset()
        if not target:
            raise UserError(_('The target record no longer exists.'))
        execution_context = self._load_json(self.execution_context_json, {})
        execution_context.update({
            'automotive_async_processing': True,
            'automotive_async_job_id': self.id,
        })
        method = getattr(target.with_context(**execution_context).with_user(self._get_execution_user()), self.target_method, None)
        if not method:
            raise UserError(_('%(model)s does not provide method %(method)s.') % {
                'model': self.target_model,
                'method': self.target_method,
            })
        args = self._load_json(self.call_args_json, [])
        kwargs = self._load_json(self.call_kwargs_json, {})
        result = method(*args, **kwargs)
        return result

    def _format_result(self, result):
        if result is None or result is False:
            return False
        if isinstance(result, dict):
            return result
        if isinstance(result, (list, tuple, set)):
            return [str(item) for item in result]
        return str(result)

    @api.model
    def _is_missing_target_error(self, exc):
        return isinstance(exc, UserError) and str(exc) == str(_('The target record no longer exists.'))

    @api.model
    def _normalize_progress_value(self, value):
        if value in (None, False, ''):
            return None
        try:
            return max(min(float(value), 100.0), 0.0)
        except Exception:
            return None

    def _call_target_progress_hook(self, hook_name):
        for job in self:
            target = job._get_target_recordset()
            if not target:
                continue
            hook = getattr(target, hook_name, None)
            if hook:
                hook(job)

    @api.model
    def is_cancel_requested(self, job_id):
        if not job_id:
            return False
        with self.pool.cursor() as status_cr:
            status_env = api.Environment(status_cr, self.env.uid, dict(self.env.context or {}))
            async_job = status_env[self._name].sudo().browse(job_id).exists()
            return bool(async_job and async_job.state == 'cancelled')

    @api.model
    def report_progress(self, job_id, progress=None, progress_message=None, state=None):
        if not job_id:
            return False

        normalized_progress = self._normalize_progress_value(progress)
        with self.pool.cursor() as progress_cr:
            progress_env = api.Environment(progress_cr, self.env.uid, dict(self.env.context or {}))
            async_job = progress_env[self._name].sudo().browse(job_id).exists()
            if not async_job:
                return False
            if async_job.state == 'cancelled':
                return False

            values = {}
            transitioned_to_running = False
            if normalized_progress is not None:
                values['progress'] = normalized_progress
            if progress_message is not None:
                values['progress_message'] = progress_message
            if state:
                if async_job.state != state:
                    values['state'] = state
                if state == 'running' and not async_job.started_at:
                    values['started_at'] = fields.Datetime.now()
                transitioned_to_running = bool(
                    state == 'running'
                    and (async_job.state != 'running' or 'started_at' in values)
                )
                if state in {'done', 'failed', 'cancelled'} and not async_job.finished_at:
                    values['finished_at'] = fields.Datetime.now()
            if not values:
                return False

            async_job.write(values)
            if transitioned_to_running:
                async_job._call_target_progress_hook('_automotive_async_on_claim')
            if async_job.batch_id:
                async_job.batch_id._sync_state_from_jobs()
            progress_cr.commit()
        return True

    def action_open_target(self):
        self.ensure_one()
        record = self._get_target_recordset()
        if not record:
            raise UserError(_('The target record no longer exists.'))
        return {
            'type': 'ir.actions.act_window',
            'name': record.display_name,
            'res_model': record._name,
            'res_id': record.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_reference(self):
        return self.action_open_source()

    def action_open_source(self):
        self.ensure_one()
        if not self.source_model or not self.source_res_id:
            raise UserError(_('This job is not linked to a source record.'))
        return {
            'type': 'ir.actions.act_window',
            'name': self.source_display_name or self.source_model,
            'res_model': self.source_model,
            'res_id': self.source_res_id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_process_now(self):
        for job in self:
            job._process_one(force=False)
        return True

    def action_retry(self):
        self.write({
            'state': 'queued',
            'next_retry_at': False,
            'started_at': False,
            'finished_at': False,
            'attempt_count': 0,
            'progress': 0.0,
            'progress_message': False,
            'last_error': False,
            'last_error_type': False,
        })
        self._call_target_progress_hook('_automotive_async_on_requeue')
        if self.batch_id:
            self.batch_id._sync_state_from_jobs()
        return True

    def action_cancel(self):
        self.write({'state': 'cancelled', 'finished_at': fields.Datetime.now()})
        self._call_target_progress_hook('_automotive_async_on_cancelled')
        if self.batch_id:
            self.batch_id._sync_state_from_jobs()
        return True

    def _process_one(self, force=False):
        self.ensure_one()
        if self.state in {'done', 'cancelled'}:
            return False
        if self.state == 'running' and not force:
            return False

        attempt_count = self.attempt_count
        executed_attempt_count = attempt_count + 1
        self.write({
            'state': 'running',
            'started_at': self.started_at or fields.Datetime.now(),
            'finished_at': False,
            'attempt_count': executed_attempt_count,
            'progress': max(self.progress, 1.0),
            'progress_message': _('Worker claimed, starting import') if self.state != 'running' else (self.progress_message or _('Processing')),
        })
        if self.batch_id:
            self.batch_id._sync_state_from_jobs()
        self._call_target_progress_hook('_automotive_async_on_claim')
        self.env.cr.commit()

        try:
            result = self.with_context(skip_automotive_async_queue=True)._execute_target_call()
        except Exception as exc:
            # SQL errors leave the transaction aborted; clear it before persisting failure state.
            self.env.cr.rollback()
            latest = self.browse(self.id).exists()
            if latest and latest.state == 'cancelled':
                latest.write({
                    'finished_at': latest.finished_at or fields.Datetime.now(),
                    'progress_message': _('Cancelled'),
                    'last_error': False,
                    'last_error_type': False,
                    'next_retry_at': False,
                })
                if latest.batch_id:
                    latest.batch_id._sync_state_from_jobs()
                return False
            if self._is_missing_target_error(exc):
                self.write({
                    'state': 'cancelled',
                    'finished_at': fields.Datetime.now(),
                    'progress_message': _('Cancelled because the target record was deleted.'),
                    'last_error': False,
                    'last_error_type': False,
                    'next_retry_at': False,
                })
                if self.batch_id:
                    self.batch_id._sync_state_from_jobs()
                return False
            retryable = executed_attempt_count < self._effective_max_attempts()
            vals = {
                'last_error': str(exc),
                'last_error_type': exc.__class__.__name__,
                'progress_message': _('Failed'),
                'finished_at': fields.Datetime.now(),
            }
            if retryable:
                vals.update({
                    'state': 'queued',
                    'next_retry_at': fields.Datetime.now() + timedelta(minutes=5),
                })
            else:
                vals['state'] = 'failed'
            self.write(vals)
            if retryable:
                self._call_target_progress_hook('_automotive_async_on_requeue')
            else:
                self._call_target_progress_hook('_automotive_async_on_failed')
            emit_runtime_event(
                {
                    'event': 'automotive_async_job_failed',
                    'category': 'async_job',
                    'source': 'automotive.async.job',
                    'level': 'error',
                    'outcome': 'failed',
                    'db': self.env.cr.dbname,
                    'uid': self.env.user.id,
                    'message': str(exc),
                    'error_type': exc.__class__.__name__,
                    'error_message': str(exc),
                    'job_id': self.id,
                    'job_type': self.job_type,
                    'batch_id': self.batch_id.id if self.batch_id else False,
                    'related_model': self.target_model or self.source_model,
                    'related_res_id': self.target_res_id or self.source_res_id,
                },
                persist_db=True,
            )
            try:
                self.message_post(body=_('Async job failed: %s') % (exc,))
            except Exception:
                _logger.exception("Failed to post async job failure message for job %s", self.id)
            if self.batch_id:
                self.batch_id._sync_state_from_jobs()
            return False

        result_json = self._dump_json(self._format_result(result))
        # Target methods report progress through a separate cursor so the UI can
        # see live milestones. Commit the target transaction before touching the
        # async-job row again, otherwise PostgreSQL can raise a serialization
        # error when this transaction tries to update the same row after those
        # progress commits.
        self.env.cr.commit()
        self.report_progress(
            self.id,
            progress=100.0,
            progress_message=_('Done'),
            state='done',
        )
        self.invalidate_recordset()
        self.write({
            'state': 'done',
            'finished_at': fields.Datetime.now(),
            'progress': 100.0,
            'progress_message': _('Done'),
            'result_json': result_json,
            'last_error': False,
            'last_error_type': False,
            'next_retry_at': False,
        })
        if self.batch_id:
            try:
                self.batch_id.message_post(body=_('Job completed: %s') % self.display_name)
            except Exception:
                _logger.exception("Failed to post async job completion message for job %s", self.id)
            self.batch_id._sync_state_from_jobs()
        return True

    @api.model
    def _recover_unexpected_job_crash(self, job_id, exc):
        error_message = str(exc) or repr(exc)
        _logger.exception('Unexpected async job crash for job %s: %s', job_id, error_message)

        def apply_recovery(job, persist_runtime_in_job_env=False):
            retryable = job.attempt_count < job._effective_max_attempts()
            values = {
                'last_error': error_message,
                'last_error_type': exc.__class__.__name__,
            }
            if retryable:
                values.update({
                    'state': 'queued',
                    'progress': 0.0,
                    'progress_message': _('Queued, waiting for worker'),
                    'started_at': False,
                    'finished_at': False,
                    'next_retry_at': fields.Datetime.now(),
                })
            else:
                values.update({
                    'state': 'failed',
                    'progress_message': _('Failed'),
                    'finished_at': fields.Datetime.now(),
                })

            job.write(values)
            if retryable:
                job._call_target_progress_hook('_automotive_async_on_requeue')
            else:
                job._call_target_progress_hook('_automotive_async_on_failed')
            runtime_event = {
                'event': 'automotive_async_job_failed',
                'category': 'async_job',
                'source': 'automotive.async.job',
                'level': 'error',
                'outcome': 'failed',
                'db': job.env.cr.dbname,
                'uid': job.env.user.id,
                'message': error_message,
                'error_type': exc.__class__.__name__,
                'error_message': error_message,
                'job_id': job.id,
                'job_type': job.job_type,
                'batch_id': job.batch_id.id if job.batch_id else False,
                'related_model': job.target_model or job.source_model,
                'related_res_id': job.target_res_id or job.source_res_id,
            }
            persisted_event = emit_runtime_event(
                runtime_event,
                persist_db=not persist_runtime_in_job_env,
            )
            if persist_runtime_in_job_env:
                job.env['automotive.audit.log'].sudo().create_runtime_event(persisted_event)
            if job.batch_id:
                job.batch_id._sync_state_from_jobs()
            return True

        with self.pool.cursor() as recovery_cr:
            recovery_env = api.Environment(recovery_cr, self.env.uid, dict(self.env.context))
            job = recovery_env[self._name].browse(job_id).exists()
            if job and job.state in {'done', 'cancelled'}:
                self.env.invalidate_all()
                return False
            if not job:
                current_job = self.browse(job_id).exists()
                if current_job and current_job.state not in {'done', 'cancelled'}:
                    apply_recovery(current_job, persist_runtime_in_job_env=True)
                    return False
                return False
            apply_recovery(job)
            recovery_cr.commit()
        self.env.invalidate_all()
        return False

    def _reconcile_target_terminal_state(self):
        reconciled = self.env[self._name]
        for job in self:
            if job.state != 'running':
                continue
            target = job._get_target_recordset()
            if not target:
                continue
            reconcile = getattr(target, '_automotive_async_reconcile_job_state', None)
            if not reconcile:
                continue
            values = reconcile(job) or {}
            if not values:
                continue
            job.write(values)
            emit_runtime_event(
                {
                    'event': 'automotive_async_job_reconciled',
                    'category': 'async_job',
                    'source': 'automotive.async.job',
                    'level': 'warning',
                    'outcome': values.get('state') or 'reconciled',
                    'db': job.env.cr.dbname,
                    'uid': job.env.uid,
                    'message': _('Reconciled stale async job from target record state.'),
                    'job_id': job.id,
                    'job_type': job.job_type,
                    'related_model': job.target_model or job.source_model,
                    'related_res_id': job.target_res_id or job.source_res_id,
                    'progress': values.get('progress', job.progress),
                    'progress_message': values.get('progress_message', job.progress_message),
                },
                persist_db=True,
            )
            reconciled |= job
        return reconciled

    @api.model
    def _requeue_stale_running_jobs(self, timeout_minutes=30):
        cutoff = fields.Datetime.now() - timedelta(minutes=max(int(timeout_minutes or 30), 1))
        stale_jobs = self.search([
            ('state', '=', 'running'),
            ('write_date', '<', cutoff),
        ])
        stale_jobs.invalidate_recordset()
        reconciled_jobs = stale_jobs._reconcile_target_terminal_state()
        stale_jobs -= reconciled_jobs
        retryable_jobs = stale_jobs.filtered(lambda job: job.attempt_count < job._effective_max_attempts())
        exhausted_jobs = stale_jobs - retryable_jobs
        if exhausted_jobs:
            exhausted_jobs.write({
                'state': 'failed',
                'progress_message': _('Failed'),
                'finished_at': fields.Datetime.now(),
                'next_retry_at': False,
                'last_error': _('Job timed out while running and retry budget is exhausted.'),
                'last_error_type': 'Timeout',
            })
            for job in exhausted_jobs:
                emit_runtime_event(
                    {
                        'event': 'automotive_async_job_stale_failed',
                        'category': 'async_job',
                        'source': 'automotive.async.job',
                        'level': 'error',
                        'outcome': 'failed',
                        'db': job.env.cr.dbname,
                        'uid': job.env.uid,
                        'message': _('Running async job timed out and retry budget is exhausted.'),
                        'job_id': job.id,
                        'job_type': job.job_type,
                        'related_model': job.target_model or job.source_model,
                        'related_res_id': job.target_res_id or job.source_res_id,
                        'progress': job.progress,
                        'progress_message': job.progress_message,
                    },
                    persist_db=True,
                )
            exhausted_jobs._call_target_progress_hook('_automotive_async_on_failed')
            exhausted_jobs.filtered('batch_id').mapped('batch_id')._sync_state_from_jobs()
        if retryable_jobs:
            stale_progress = {
                job.id: {
                    'progress': job.progress,
                    'progress_message': job.progress_message,
                }
                for job in retryable_jobs
            }
            retryable_jobs.write({
                'state': 'queued',
                'progress': 0.0,
                'progress_message': _('Queued, waiting for worker'),
                'started_at': False,
                'finished_at': False,
                'last_error': False,
                'last_error_type': False,
                'next_retry_at': fields.Datetime.now(),
            })
            for job in retryable_jobs:
                previous = stale_progress.get(job.id, {})
                emit_runtime_event(
                    {
                        'event': 'automotive_async_job_stale_requeued',
                        'category': 'async_job',
                        'source': 'automotive.async.job',
                        'level': 'warning',
                        'outcome': 'requeued',
                        'db': job.env.cr.dbname,
                        'uid': job.env.uid,
                        'message': _('Running async job heartbeat expired; requeued for retry.'),
                        'job_id': job.id,
                        'job_type': job.job_type,
                        'related_model': job.target_model or job.source_model,
                        'related_res_id': job.target_res_id or job.source_res_id,
                        'stale_progress': previous.get('progress'),
                        'stale_progress_message': previous.get('progress_message'),
                    },
                    persist_db=True,
                )
            retryable_jobs._call_target_progress_hook('_automotive_async_on_requeue')
            retryable_jobs.filtered('batch_id').mapped('batch_id')._sync_state_from_jobs()
        return stale_jobs

    @api.model
    def _fail_exhausted_queued_jobs(self):
        jobs = self.search([
            ('state', '=', 'queued'),
            ('attempt_count', '>', 0),
        ])
        exhausted = jobs.filtered(lambda job: job.attempt_count >= job._effective_max_attempts())
        if not exhausted:
            return exhausted
        exhausted.write({
            'state': 'failed',
            'progress_message': _('Failed'),
            'finished_at': fields.Datetime.now(),
            'next_retry_at': False,
            'last_error': _('Retry skipped because retry budget is exhausted.'),
            'last_error_type': 'RetrySkipped',
        })
        exhausted._call_target_progress_hook('_automotive_async_on_failed')
        exhausted.filtered('batch_id').mapped('batch_id')._sync_state_from_jobs()
        return exhausted

    @api.model
    def _claim_job_ids(self, limit):
        if limit <= 0:
            return []
        self.env.cr.execute(
            """
            SELECT id
              FROM automotive_async_job
             WHERE state = 'queued'
               AND scheduled_at <= NOW()
               AND (next_retry_at IS NULL OR next_retry_at <= NOW())
             ORDER BY priority ASC, scheduled_at ASC, id ASC
             FOR UPDATE SKIP LOCKED
             LIMIT %s
            """,
            [limit],
        )
        claim_ids = [row[0] for row in self.env.cr.fetchall()]
        claimed_jobs = self.browse(claim_ids).exists()
        if claimed_jobs:
            for job in claimed_jobs:
                progress_message = job.progress_message
                if not progress_message or progress_message == _('Queued, waiting for worker'):
                    progress_message = _('Worker claimed, starting import')
                job.write({
                    'state': 'running',
                    'started_at': job.started_at or fields.Datetime.now(),
                    'progress': job.progress if job.progress > 0 else 1.0,
                    'progress_message': progress_message,
                })
            claimed_jobs._call_target_progress_hook('_automotive_async_on_claim')
            claimed_jobs.filtered('batch_id').mapped('batch_id')._sync_state_from_jobs()
        return claim_ids

    @api.model
    def cron_process_jobs(self, limit=None):
        icp = self.env['ir.config_parameter'].sudo()
        limit = int(limit or icp.get_param('automotive.async_jobs_per_run') or 20)
        stale_timeout = int(icp.get_param('automotive.async_running_timeout_minutes') or 3)
        self._requeue_stale_running_jobs(timeout_minutes=stale_timeout)
        self._fail_exhausted_queued_jobs()

        processed = 0
        attempted = 0
        while attempted < limit:
            claim_ids = self._claim_job_ids(1)
            if not claim_ids:
                break
            self.env.cr.commit()
            for job_id in claim_ids:
                attempted += 1
                try:
                    with self.pool.cursor() as job_cr:
                        job_env = api.Environment(job_cr, self.env.uid, dict(self.env.context))
                        job = job_env[self._name].browse(job_id).exists()
                        if not job:
                            continue
                        if job._process_one(force=True):
                            processed += 1
                        job_cr.commit()
                except Exception as exc:  # noqa: BLE001
                    self._recover_unexpected_job_crash(job_id, exc)
                if attempted >= limit:
                    break
        return processed
