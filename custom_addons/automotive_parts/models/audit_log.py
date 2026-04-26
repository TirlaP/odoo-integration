# -*- coding: utf-8 -*-
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
import logging
from collections.abc import Mapping, Sequence

from odoo import models, fields, api
from odoo.osv import expression

_logger = logging.getLogger(__name__)


class AutomotiveAuditLog(models.Model):
    """Audit Log for tracking all system changes"""
    _name = 'automotive.audit.log'
    _description = 'Audit Log'
    _order = 'create_date desc'
    _SENSITIVE_KEY_PARTS = (
        'token',
        'secret',
        'password',
        'authorization_code',
        'api_key',
        'client_secret',
        'refresh_token',
        'access_token',
        'oauth_state',
    )
    _REDACTED_VALUE = '[redacted]'
    _MAX_PAYLOAD_CHARS = 64000

    user_id = fields.Many2one('res.users', 'User', required=True, default=lambda self: self.env.user)
    company_id = fields.Many2one('res.company', 'Company', readonly=True, index=True)
    action = fields.Selection([
        ('create', 'Create'),
        ('write', 'Modify'),
        ('unlink', 'Delete'),
        ('custom', 'Custom Action'),
    ], string='Action', required=True, index=True)
    log_type = fields.Selection([
        ('audit', 'Audit'),
        ('runtime', 'Runtime'),
    ], string='Type', required=True, default='audit', index=True)

    level = fields.Selection([
        ('debug', 'Debug'),
        ('info', 'Info'),
        ('warning', 'Warning'),
        ('error', 'Error'),
        ('critical', 'Critical'),
    ], string='Level', index=True)
    category = fields.Char('Category', index=True)
    event = fields.Char('Event', index=True)
    source = fields.Char('Source', index=True)
    outcome = fields.Char('Outcome', index=True)
    db_name = fields.Char('Database', index=True)
    request_method = fields.Char('HTTP Method', index=True)
    request_path = fields.Char('Request Path', index=True)
    related_model = fields.Char('Related Model', index=True)
    related_res_id = fields.Integer('Related Record ID', index=True)
    message = fields.Text('Message')
    payload_json = fields.Text('Payload JSON')
    legacy_runtime_log_id = fields.Integer('Legacy Runtime Log ID', index=True, copy=False)

    model_name = fields.Char('Model', required=True, index=True)
    model_description = fields.Char('Model Label', readonly=True, index=True)
    record_id = fields.Integer('Record ID', index=True)
    record_display_name = fields.Char('Record', readonly=True, index=True)
    description = fields.Text('Description')

    old_values = fields.Text('Old Values')
    new_values = fields.Text('New Values')
    change_summary = fields.Text('Changes', compute='_compute_change_summary')

    create_date = fields.Datetime('Date & Time', readonly=True, index=True)

    @api.model
    def _runtime_int_or_false(self, value):
        return int(value) if str(value).isdigit() else False

    @api.model
    def _runtime_model_description(self, model_name):
        if not model_name:
            return 'Runtime Log'
        model = self.env['ir.model'].sudo().search([('model', '=', model_name)], limit=1)
        return model.name if model else model_name

    @api.model
    def _runtime_level(self, value):
        level = str(value or 'info').strip().lower()
        return level if level in {'debug', 'info', 'warning', 'error', 'critical'} else 'info'

    @classmethod
    def _is_sensitive_key(cls, key):
        if not key:
            return False
        normalized = str(key).strip().lower()
        return any(part in normalized for part in cls._SENSITIVE_KEY_PARTS)

    @classmethod
    def _sanitize_payload(cls, payload, key=None):
        if payload in (None, False, ''):
            return payload
        if key and cls._is_sensitive_key(key):
            return cls._REDACTED_VALUE
        if isinstance(payload, models.BaseModel):
            return {
                'model': payload._name,
                'ids': payload.ids,
                'display_name': payload.display_name if len(payload) == 1 else False,
            }
        if isinstance(payload, (datetime, date)):
            return payload.isoformat()
        if isinstance(payload, Decimal):
            return str(payload)
        if isinstance(payload, bytes):
            return payload.decode('utf-8', errors='replace')
        if isinstance(payload, dict):
            return {
                str(dict_key): cls._sanitize_payload(value, key=dict_key)
                for dict_key, value in payload.items()
            }
        if isinstance(payload, Mapping):
            return {
                str(dict_key): cls._sanitize_payload(value, key=dict_key)
                for dict_key, value in payload.items()
            }
        if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
            if len(payload) == 2 and cls._is_sensitive_key(payload[0]):
                return cls._REDACTED_VALUE
            return [cls._sanitize_payload(value) for value in payload]
        if isinstance(payload, set):
            return [cls._sanitize_payload(value) for value in sorted(payload, key=str)]
        return payload

    @classmethod
    def _truncate_payload(cls, payload):
        if payload in (None, False):
            return payload
        if not isinstance(payload, str):
            payload = str(payload)
        if len(payload) <= cls._MAX_PAYLOAD_CHARS:
            return payload
        return f"{payload[:cls._MAX_PAYLOAD_CHARS]}... [truncated {len(payload) - cls._MAX_PAYLOAD_CHARS} chars]"

    @staticmethod
    def _stringify_payload(payload):
        if payload is None or payload is False:
            return False
        payload = AutomotiveAuditLog._sanitize_payload(payload)
        if isinstance(payload, str):
            return AutomotiveAuditLog._truncate_payload(payload)
        rendered = json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True, separators=(',', ':'))
        return AutomotiveAuditLog._truncate_payload(rendered)

    @staticmethod
    def _parse_payload(payload):
        if not payload:
            return {}
        if isinstance(payload, (dict, list)):
            return payload
        if not isinstance(payload, str):
            return payload
        try:
            return json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return payload

    @classmethod
    def _format_value(cls, value):
        if value in (None, False, ''):
            return 'empty'
        if isinstance(value, dict):
            return ', '.join(f"{key}: {cls._format_value(sub_value)}" for key, sub_value in value.items()) or 'empty'
        if isinstance(value, list):
            return ', '.join(cls._format_value(item) for item in value) or 'empty'
        return str(value)

    @api.depends('old_values', 'new_values')
    def _compute_change_summary(self):
        for log in self:
            old_payload = self._parse_payload(log.old_values)
            new_payload = self._parse_payload(log.new_values)

            if isinstance(old_payload, dict) or isinstance(new_payload, dict):
                old_dict = old_payload if isinstance(old_payload, dict) else {}
                new_dict = new_payload if isinstance(new_payload, dict) else {}
                keys = sorted(set(old_dict) | set(new_dict))
                lines = []
                for key in keys:
                    old_value = self._format_value(old_dict.get(key))
                    new_value = self._format_value(new_dict.get(key))
                    if old_value == new_value:
                        lines.append(f"{key}: {new_value}")
                    else:
                        lines.append(f"{key}: {old_value} -> {new_value}")
                log.change_summary = '\n'.join(lines) if lines else False
                continue

            if isinstance(new_payload, list):
                log.change_summary = '\n'.join(self._format_value(item) for item in new_payload) or False
                continue

            if new_payload not in ({}, [], False, None, ''):
                log.change_summary = self._format_value(new_payload)
                continue

            if old_payload not in ({}, [], False, None, ''):
                log.change_summary = self._format_value(old_payload)
                continue

            log.change_summary = False

    @api.model
    def log_change(self, action, record, description=None, old_values=None, new_values=None):
        record.ensure_one()
        company = (
            record.company_id
            if 'company_id' in record._fields and record.company_id
            else self.env.company
        )
        values = {
            'user_id': self.env.user.id,
            'company_id': company.id if company else False,
            'action': action,
            'model_name': record._name,
            'model_description': record._description or record._name,
            'record_id': record.id,
            'record_display_name': record.display_name,
            'description': description or '',
            'old_values': self._stringify_payload(old_values),
            'new_values': self._stringify_payload(new_values),
        }
        try:
            # Audit logging must never block the business action that triggered it.
            with self.env.cr.savepoint():
                return self.create(values)
        except Exception:
            _logger.exception(
                'Skipping automotive audit log write for %s[%s] during %s',
                record._name,
                record.id,
                action,
            )
            return self.browse()

    @api.model
    def create_runtime_event(self, event):
        payload = dict(event or {})
        user_id = self._runtime_int_or_false(payload.get('uid') or payload.get('user_id'))
        related_res_id = self._runtime_int_or_false(payload.get('related_res_id'))
        related_model = payload.get('related_model') or False
        message = self._truncate_payload(
            payload.get('message')
            or payload.get('error_message')
            or payload.get('description')
            or ''
        )
        model_name = related_model or 'automotive.runtime.log'
        values = {
            'log_type': 'runtime',
            'user_id': user_id or self.env.user.id,
            'company_id': self.env.company.id if self.env.company else False,
            'action': 'custom',
            'model_name': model_name,
            'model_description': self._runtime_model_description(model_name),
            'record_id': related_res_id or False,
            'record_display_name': payload.get('record_display_name') or payload.get('event') or model_name,
            'description': message,
            'level': self._runtime_level(payload.get('level')),
            'category': payload.get('category'),
            'event': payload.get('event') or 'runtime_event',
            'source': payload.get('source'),
            'outcome': payload.get('outcome'),
            'db_name': payload.get('db') or payload.get('db_name'),
            'request_method': payload.get('method') or payload.get('request_method'),
            'request_path': payload.get('path') or payload.get('request_path'),
            'related_model': related_model,
            'related_res_id': related_res_id or False,
            'message': message,
            'payload_json': self._stringify_payload(payload),
        }
        return self.sudo().create(values)

    @api.model
    def cron_cleanup_runtime_logs(self):
        days = int(self.env['ir.config_parameter'].sudo().get_param(
            'automotive.runtime_log_retention_days', 30
        ) or 30)
        cutoff = fields.Datetime.now() - timedelta(days=max(days, 1))
        stale_logs = self.sudo().search([
            ('log_type', '=', 'runtime'),
            ('create_date', '<', cutoff),
        ])
        if stale_logs:
            stale_logs.unlink()
        return len(stale_logs)

    def name_get(self):
        """Custom name display"""
        result = []
        for log in self:
            if log.log_type == 'runtime':
                label = log.message or log.event or log.record_display_name or log.model_name
                name = f"{log.user_id.name} - {log.level or 'info'} - {label}"
            else:
                name = f"{log.user_id.name} - {log.action} - {log.record_display_name or log.model_name}"
            result.append((log.id, name))
        return result

    @api.model
    def name_search(self, name='', args=None, operator='ilike', limit=100):
        args = list(args or [])
        if not name:
            return self.search(args, limit=limit).name_get()

        search_domain = expression.OR([
            [('model_name', operator, name)],
            [('model_description', operator, name)],
            [('record_display_name', operator, name)],
            [('description', operator, name)],
            [('level', operator, name)],
            [('category', operator, name)],
            [('event', operator, name)],
            [('request_path', operator, name)],
            [('message', operator, name)],
            [('old_values', operator, name)],
            [('new_values', operator, name)],
            [('payload_json', operator, name)],
            [('user_id.name', operator, name)],
            [('company_id.name', operator, name)],
        ])
        return self.search(expression.AND([args, search_domain]), limit=limit).name_get()
