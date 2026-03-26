# -*- coding: utf-8 -*-
import json
import logging
import os
import threading
import time
import traceback

import odoo.http


_logger = logging.getLogger(__name__)
_PATCH_FLAG = "_automotive_request_trace_patched"
_DEFAULT_TRACE_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "runtime_trace.log")
)


def _trace_enabled():
    value = os.getenv("AUTOMOTIVE_HTTP_TRACE", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _write_trace_payload(event):
    payload = json.dumps(event, ensure_ascii=True, sort_keys=True)
    trace_file = os.getenv("AUTOMOTIVE_HTTP_TRACE_FILE", _DEFAULT_TRACE_FILE)
    try:
        with open(trace_file, "a", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")
    except OSError:
        _logger.exception("Failed to write automotive HTTP trace file")

    if event.get("outcome") in {"error", "exception"}:
        _logger.error(payload)
    else:
        _logger.info(payload)


def _patch_dispatcher_error_handlers():
    for dispatcher_cls in (odoo.http.HttpDispatcher, odoo.http.JsonRPCDispatcher):
        patch_flag = f"{_PATCH_FLAG}_{dispatcher_cls.__name__}_handle_error"
        if getattr(dispatcher_cls, patch_flag, False):
            continue

        original_handle_error = dispatcher_cls.handle_error

        def traced_handle_error(self, exc, _original=original_handle_error, _dispatcher_name=dispatcher_cls.__name__):
            if _trace_enabled():
                thread = threading.current_thread()
                req = getattr(self, "request", None)
                event = {
                    "event": "automotive_http_exception",
                    "dispatcher": _dispatcher_name,
                    "method": getattr(getattr(req, "httprequest", None), "method", None),
                    "path": getattr(getattr(req, "httprequest", None), "path", None),
                    "query_string": getattr(getattr(req, "httprequest", None), "query_string", b"").decode("utf-8", "replace")
                    if getattr(getattr(req, "httprequest", None), "query_string", None) is not None else "",
                    "remote_addr": getattr(getattr(req, "httprequest", None), "remote_addr", None),
                    "db": getattr(thread, "dbname", None),
                    "uid": getattr(thread, "uid", None),
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                    "outcome": "exception",
                }
                _write_trace_payload(event)
            return _original(self, exc)

        dispatcher_cls.handle_error = traced_handle_error
        setattr(dispatcher_cls, patch_flag, True)


def _patch_http_application():
    if getattr(odoo.http.Application, _PATCH_FLAG, False):
        return

    original_call = odoo.http.Application.__call__

    def traced_call(self, environ, start_response):
        if not _trace_enabled():
            return original_call(self, environ, start_response)

        started_at = time.time()
        thread = threading.current_thread()
        event = {
            "event": "automotive_http_request",
            "method": environ.get("REQUEST_METHOD"),
            "path": environ.get("PATH_INFO"),
            "query_string": environ.get("QUERY_STRING") or "",
            "host": environ.get("HTTP_HOST"),
            "remote_addr": environ.get("REMOTE_ADDR"),
            "user_agent": environ.get("HTTP_USER_AGENT"),
        }

        captured = {"status": None, "content_type": None}

        def traced_start_response(status, headers, exc_info=None):
            captured["status"] = status
            for key, value in headers:
                if key.lower() == "content-type":
                    captured["content_type"] = value
                    break
            return start_response(status, headers, exc_info)

        try:
            response = original_call(self, environ, traced_start_response)
            return response
        except Exception as exc:  # noqa: BLE001 - tracing should not mask failures
            event.update({
                "outcome": "exception",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            })
            raise
        finally:
            event.update({
                "duration_ms": round((time.time() - started_at) * 1000, 2),
                "status": captured["status"],
                "content_type": captured["content_type"],
                "db": getattr(thread, "dbname", None),
                "uid": getattr(thread, "uid", None),
                "query_count": getattr(thread, "query_count", None),
                "query_time": round(getattr(thread, "query_time", 0) or 0, 6),
            })

            if "outcome" not in event:
                status_code = 0
                if captured["status"]:
                    try:
                        status_code = int(str(captured["status"]).split(" ", 1)[0])
                    except (ValueError, IndexError):
                        status_code = 0
                event["outcome"] = "error" if status_code >= 500 else "success"

            _write_trace_payload(event)

    odoo.http.Application.__call__ = traced_call
    setattr(odoo.http.Application, _PATCH_FLAG, True)


_patch_dispatcher_error_handlers()
_patch_http_application()
