# -*- coding: utf-8 -*-
import json
from datetime import timedelta

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
        payload = dict(event or {})
        user_id = payload.get("uid") or payload.get("user_id")
        related_res_id = payload.get("related_res_id")
        values = {
            "level": str(payload.get("level") or "info").lower(),
            "category": payload.get("category"),
            "event": payload.get("event") or "runtime_event",
            "source": payload.get("source"),
            "outcome": payload.get("outcome"),
            "db_name": payload.get("db") or payload.get("db_name"),
            "user_id": int(user_id) if str(user_id).isdigit() else False,
            "request_method": payload.get("method") or payload.get("request_method"),
            "request_path": payload.get("path") or payload.get("request_path"),
            "related_model": payload.get("related_model"),
            "related_res_id": int(related_res_id) if str(related_res_id).isdigit() else False,
            "message": self._truncate_payload(
                payload.get("message")
                or payload.get("error_message")
                or payload.get("description")
            ),
            "payload_json": self._truncate_payload(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
            ),
        }
        return self.sudo().create(values)

    @api.model
    def cron_cleanup_old_logs(self):
        days = int(self.env["ir.config_parameter"].sudo().get_param(
            "automotive.runtime_log_retention_days", 30
        ) or 30)
        cutoff = fields.Datetime.now() - timedelta(days=max(days, 1))
        stale_logs = self.sudo().search([("create_date", "<", cutoff)])
        if stale_logs:
            stale_logs.unlink()
        return len(stale_logs)
