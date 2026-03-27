# -*- coding: utf-8 -*-
import json

from odoo import models, fields, api


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
    )
    _REDACTED_VALUE = '[redacted]'

    user_id = fields.Many2one('res.users', 'User', required=True, default=lambda self: self.env.user)
    company_id = fields.Many2one('res.company', 'Company', readonly=True, index=True)
    action = fields.Selection([
        ('create', 'Create'),
        ('write', 'Modify'),
        ('unlink', 'Delete'),
        ('custom', 'Custom Action'),
    ], string='Action', required=True)

    model_name = fields.Char('Model', required=True)
    model_description = fields.Char('Model Label', readonly=True, index=True)
    record_id = fields.Integer('Record ID')
    record_display_name = fields.Char('Record', readonly=True, index=True)
    description = fields.Text('Description')

    old_values = fields.Text('Old Values')
    new_values = fields.Text('New Values')

    create_date = fields.Datetime('Date & Time', readonly=True)

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
        if isinstance(payload, dict):
            return {
                dict_key: cls._sanitize_payload(value, key=dict_key)
                for dict_key, value in payload.items()
            }
        if isinstance(payload, list):
            return [cls._sanitize_payload(value) for value in payload]
        if isinstance(payload, tuple):
            return tuple(cls._sanitize_payload(value) for value in payload)
        if isinstance(payload, set):
            return [cls._sanitize_payload(value) for value in sorted(payload, key=str)]
        return payload

    @staticmethod
    def _stringify_payload(payload):
        if payload is None or payload is False:
            return False
        payload = AutomotiveAuditLog._sanitize_payload(payload)
        if isinstance(payload, str):
            return payload
        return json.dumps(payload, ensure_ascii=False, default=str)

    @api.model
    def log_change(self, action, record, description=None, old_values=None, new_values=None):
        record.ensure_one()
        company = (
            record.company_id
            if 'company_id' in record._fields and record.company_id
            else self.env.company
        )
        return self.create({
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
        })

    def name_get(self):
        """Custom name display"""
        result = []
        for log in self:
            name = f"{log.user_id.name} - {log.action} - {log.record_display_name or log.model_name}"
            result.append((log.id, name))
        return result
