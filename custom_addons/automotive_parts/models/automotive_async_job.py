# -*- coding: utf-8 -*-
import json
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError


def _json_dumps(value):
    return json.dumps(value or [], ensure_ascii=False, default=str)


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
        ('invoice.ingest.job', '_process_ingest_job'),
        ('ir.actions.report', '_run_automotive_async_label_job'),
    }

    @classmethod
    def _is_allowed_target(cls, target_model, target_method):
        return (target_model, target_method) in cls._ALLOWED_TARGETS

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
        if job_type == 'invoice_ingest':
            target_model = target_model or 'invoice.ingest.job'
            target_method = target_method or '_process_ingest_job'
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
            'progress': 0.0,
            'progress_message': False,
            'last_error': False,
            'last_error_type': False,
        })
        if self.batch_id:
            self.batch_id._sync_state_from_jobs()
        return True

    def action_cancel(self):
        self.write({'state': 'cancelled', 'finished_at': fields.Datetime.now()})
        if self.batch_id:
            self.batch_id._sync_state_from_jobs()
        return True

    def _process_one(self, force=False):
        self.ensure_one()
        if self.state in {'done', 'cancelled'}:
            return False
        if self.state == 'running' and not force:
            return False

        self.write({
            'state': 'running',
            'started_at': self.started_at or fields.Datetime.now(),
            'finished_at': False,
            'attempt_count': self.attempt_count + (0 if force else 1),
            'progress': max(self.progress, 1.0),
            'progress_message': _('Processing'),
        })
        if self.batch_id:
            self.batch_id._sync_state_from_jobs()

        try:
            result = self.with_context(skip_automotive_async_queue=True)._execute_target_call()
        except Exception as exc:
            retryable = self.attempt_count < self.max_attempts
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
            self.message_post(body=_('Async job failed: %s') % (exc,))
            if self.batch_id:
                self.batch_id._sync_state_from_jobs()
            raise

        self.write({
            'state': 'done',
            'finished_at': fields.Datetime.now(),
            'progress': 100.0,
            'progress_message': _('Done'),
            'result_json': self._dump_json(self._format_result(result)),
            'last_error': False,
            'last_error_type': False,
            'next_retry_at': False,
        })
        if self.batch_id:
            self.batch_id.message_post(body=_('Job completed: %s') % self.display_name)
            self.batch_id._sync_state_from_jobs()
        return True

    @api.model
    def _requeue_stale_running_jobs(self, timeout_minutes=30):
        cutoff = fields.Datetime.now() - timedelta(minutes=max(int(timeout_minutes or 30), 1))
        stale_jobs = self.search([
            ('state', '=', 'running'),
            ('started_at', '<', cutoff),
        ])
        if stale_jobs:
            stale_jobs.write({
                'state': 'queued',
                'progress_message': _('Requeued after stale worker timeout'),
                'next_retry_at': fields.Datetime.now(),
            })
        return stale_jobs

    @api.model
    def _claim_job_ids(self, limit):
        if limit <= 0:
            return []
        self.env.cr.execute(
            """
            WITH next_jobs AS (
                SELECT id
                FROM automotive_async_job
                WHERE state = 'queued'
                  AND scheduled_at <= NOW()
                  AND (next_retry_at IS NULL OR next_retry_at <= NOW())
                ORDER BY priority ASC, scheduled_at ASC, id ASC
                FOR UPDATE SKIP LOCKED
                LIMIT %s
            )
            UPDATE automotive_async_job job
               SET state = 'running',
                   started_at = COALESCE(job.started_at, NOW()),
                   attempt_count = job.attempt_count + 1,
                   progress_message = 'Running'
              FROM next_jobs
             WHERE job.id = next_jobs.id
            RETURNING job.id
            """,
            [limit],
        )
        return [row[0] for row in self.env.cr.fetchall()]

    @api.model
    def cron_process_jobs(self):
        icp = self.env['ir.config_parameter'].sudo()
        limit = int(icp.get_param('automotive.async_jobs_per_run') or 20)
        stale_timeout = int(icp.get_param('automotive.async_running_timeout_minutes') or 30)
        self._requeue_stale_running_jobs(timeout_minutes=stale_timeout)

        processed = 0
        while processed < limit:
            remaining = limit - processed
            claim_ids = self._claim_job_ids(min(remaining, 10))
            if not claim_ids:
                break
            for job in self.browse(claim_ids):
                with self.env.cr.savepoint():
                    job._process_one(force=True)
                processed += 1
                if processed >= limit:
                    break
        return processed
