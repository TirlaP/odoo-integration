# -*- coding: utf-8 -*-
import json
import logging
import os
from datetime import datetime, timezone

from odoo import http
from odoo.http import Response, request


_logger = logging.getLogger(__name__)
_TRACE_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "runtime_trace.log"))


def _append_trace(event):
    payload = json.dumps(event, ensure_ascii=True, sort_keys=True)
    try:
        with open(_TRACE_FILE, "a", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")
    except OSError:
        _logger.exception("Failed to write browser diagnostic trace")
    _logger.error(payload)


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
