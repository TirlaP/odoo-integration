# -*- coding: utf-8 -*-
import json
import logging
import os
from datetime import datetime, timezone
from time import monotonic
from urllib import error as urllib_error
from urllib import request as urllib_request

import odoo
from odoo import SUPERUSER_ID, api


_logger = logging.getLogger("odoo.addons.automotive_parts.runtime")
_TRACE_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "runtime_trace.log")
)
_MAX_PAYLOAD_CHARS = 64000
_BETTER_STACK_FAILURE_LOG_INTERVAL_SECONDS = 60
_better_stack_last_failure_at = 0.0


def _trace_file_enabled():
    value = os.getenv("AUTOMOTIVE_TRACE_FILE_ENABLED", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _normalize_event(event):
    payload = dict(event or {})
    payload.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    payload.setdefault("service", "odoo-integration")
    payload.setdefault("deployment_id", os.getenv("RAILWAY_DEPLOYMENT_ID"))
    payload.setdefault("git_commit", os.getenv("RAILWAY_GIT_COMMIT_SHA"))
    payload.setdefault("environment", os.getenv("RAILWAY_ENVIRONMENT_NAME") or os.getenv("RAILWAY_ENVIRONMENT"))
    return payload


def _truncate_string(value):
    text = str(value)
    if len(text) <= _MAX_PAYLOAD_CHARS:
        return text
    return f"{text[:_MAX_PAYLOAD_CHARS]}... [truncated {len(text) - _MAX_PAYLOAD_CHARS} chars]"


def _prepare_payload(value):
    if isinstance(value, dict):
        return {str(key): _prepare_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_prepare_payload(item) for item in value]
    if value is None or value is False:
        return value
    if isinstance(value, str):
        return _truncate_string(value)
    return value


def _serialize(payload):
    return json.dumps(_prepare_payload(payload), ensure_ascii=True, sort_keys=True, default=str)


def _write_trace_file(rendered):
    if not _trace_file_enabled():
        return
    try:
        with open(_TRACE_FILE, "a", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.write("\n")
    except OSError:
        _logger.exception("Failed to write automotive runtime trace file")


def _persist_runtime_event(event):
    db_name = (event.get("db") or event.get("db_name") or "").strip()
    if not db_name:
        return
    try:
        registry = odoo.modules.registry.Registry(db_name)
    except Exception:
        return

    try:
        with registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            if "automotive.runtime.log" not in env:
                return
            env["automotive.runtime.log"].sudo().create_from_event(event)
            cr.commit()
    except Exception:
        _logger.exception("Failed to persist automotive runtime event")


def _better_stack_url():
    token = (os.getenv("BETTER_STACK_SOURCE_TOKEN") or "").strip()
    host = (os.getenv("BETTER_STACK_INGESTING_HOST") or "").strip()
    if not token or not host:
        return None, None
    if host.startswith("http://") or host.startswith("https://"):
        url = host
    else:
        url = f"https://{host}"
    return url.rstrip("/"), token


def _log_better_stack_failure_once(message):
    global _better_stack_last_failure_at
    now = monotonic()
    if now - _better_stack_last_failure_at < _BETTER_STACK_FAILURE_LOG_INTERVAL_SECONDS:
        return
    _better_stack_last_failure_at = now
    _logger.warning(message)


def _send_to_better_stack(rendered):
    url, token = _better_stack_url()
    if not url or not token:
        return

    request = urllib_request.Request(
        url,
        data=rendered.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=1.5) as response:
            response.read()
    except (urllib_error.URLError, urllib_error.HTTPError, TimeoutError, ValueError) as exc:
        _log_better_stack_failure_once(
            f"Failed to forward automotive runtime event to Better Stack: {exc}"
        )


def emit_runtime_event(event, level=None, persist_db=False):
    payload = _normalize_event(event)
    rendered = _serialize(payload)
    log_level = level or (
        logging.ERROR
        if payload.get("outcome") in {"error", "exception", "failed"}
        else logging.INFO
    )

    _write_trace_file(rendered)
    _logger.log(log_level, rendered)
    _send_to_better_stack(rendered)

    if persist_db:
        _persist_runtime_event(payload)

    return payload
