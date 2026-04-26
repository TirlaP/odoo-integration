# -*- coding: utf-8 -*-
from odoo import api, fields, models


class AutomotiveRuntimeLog(models.Model):
    _name = "automotive.runtime.log"
    _description = "Automotive Runtime Log"
    _order = "create_date desc, id desc"

    create_date = fields.Datetime("Created On", readonly=True, index=True)
    level = fields.Selection(
        [
            ("debug", "Debug"),
            ("info", "Info"),
            ("warning", "Warning"),
            ("error", "Error"),
            ("critical", "Critical"),
        ],
        required=True,
        default="info",
        index=True,
    )
    category = fields.Char("Category", index=True)
    event = fields.Char("Event", required=True, index=True)
    source = fields.Char("Source", index=True)
    outcome = fields.Char("Outcome", index=True)
    db_name = fields.Char("Database", index=True)
    user_id = fields.Many2one("res.users", string="User", index=True, ondelete="set null")
    request_method = fields.Char("HTTP Method", index=True)
    request_path = fields.Char("Request Path", index=True)
    related_model = fields.Char("Related Model", index=True)
    related_res_id = fields.Integer("Related Record ID", index=True)
    message = fields.Text("Message")
    payload_json = fields.Text("Payload JSON")

    @api.model
    def _truncate_payload(self, text, limit=64000):
        if not text:
            return False
        value = str(text)
        if len(value) <= limit:
            return value
        return f"{value[:limit]}... [truncated {len(value) - limit} chars]"

    @api.model
    def create_from_event(self, event):
        return self.env["automotive.audit.log"].sudo().create_runtime_event(event)

    @api.model
    def cron_cleanup_old_logs(self):
        return self.env["automotive.audit.log"].sudo().cron_cleanup_runtime_logs()
