# -*- coding: utf-8 -*-
import json
from datetime import datetime, timezone

from odoo import http
from odoo.http import Response, request

from ..runtime_logging import emit_runtime_event

def _append_trace(event):
    emit_runtime_event(
        {
            **dict(event or {}),
            "category": "browser",
            "source": "browser_diagnostics",
            "level": "error",
        },
        persist_db=True,
    )


class AutomotiveBrowserDiagnosticsController(http.Controller):
    @http.route(
        '/automotive/browser-diagnostics',
        type='http',
        auth='none',
        methods=['POST'],
        csrf=False,
        save_session=False,
    )
    def browser_diagnostics(self, **payload):
        raw_body = request.httprequest.get_data(as_text=True) or "{}"
        try:
            incoming = json.loads(raw_body)
        except json.JSONDecodeError:
            incoming = {"raw_body": raw_body}

        event = {
            "event": "automotive_browser_error",
            "received_at": datetime.now(timezone.utc).isoformat(),
            "db": request.db,
            "method": request.httprequest.method,
            "path": request.httprequest.path,
            "remote_addr": request.httprequest.remote_addr,
            "user_agent": request.httprequest.user_agent.string,
        }
        if isinstance(incoming, dict):
            event.update(incoming)
        else:
            event["payload"] = incoming
        _append_trace(event)
        return Response('{"ok": true}', content_type='application/json')
