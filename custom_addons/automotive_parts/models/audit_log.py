# -*- coding: utf-8 -*-
import json

from odoo import models, fields, api


class AutomotiveAuditLog(models.Model):
    """Audit Log for tracking all system changes"""
    _name = 'automotive.audit.log'
    _description = 'Audit Log'
    _order = 'create_date desc'

    user_id = fields.Many2one('res.users', 'User', required=True, default=lambda self: self.env.user)
    action = fields.Selection([
        ('create', 'Create'),
        ('write', 'Modify'),
        ('unlink', 'Delete'),
        ('custom', 'Custom Action'),
    ], string='Action', required=True)

    model_name = fields.Char('Model', required=True)
    record_id = fields.Integer('Record ID')
    description = fields.Text('Description')

    old_values = fields.Text('Old Values')
    new_values = fields.Text('New Values')

    create_date = fields.Datetime('Date & Time', readonly=True)

    @staticmethod
    def _stringify_payload(payload):
        if payload is None or payload is False:
            return False
        if isinstance(payload, str):
            return payload
        return json.dumps(payload, ensure_ascii=False, default=str)

    @api.model
    def log_change(self, action, record, description=None, old_values=None, new_values=None):
        record.ensure_one()
        return self.create({
            'user_id': self.env.user.id,
            'action': action,
            'model_name': record._name,
            'record_id': record.id,
            'description': description or '',
            'old_values': self._stringify_payload(old_values),
            'new_values': self._stringify_payload(new_values),
        })

    def name_get(self):
        """Custom name display"""
        result = []
        for log in self:
            name = f"{log.user_id.name} - {log.action} - {log.model_name}"
            result.append((log.id, name))
        return result
