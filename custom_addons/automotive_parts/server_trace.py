# -*- coding: utf-8 -*-
import contextlib
import logging
import os
import select
import threading
import time
import traceback

import odoo
from odoo.service import server as odoo_server

from .runtime_logging import emit_runtime_event

_logger = logging.getLogger(__name__)
_PATCH_FLAG = "_automotive_server_trace_patched"


def _trace_enabled():
    value = os.getenv("AUTOMOTIVE_SERVER_TRACE", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _write_trace_payload(event):
    emit_runtime_event(
        {
            **dict(event or {}),
            "category": "cron",
            "source": "server_trace",
            "level": "error",
        },
        persist_db=True,
    )


def _patch_cron_thread():
    server_cls = odoo_server.ThreadedServer
    if getattr(server_cls, _PATCH_FLAG, False):
        return

    def traced_cron_thread(self, number):
        from odoo.addons.base.models.ir_cron import ir_cron

        phase = "init"

        def _run_cron(cr):
            nonlocal phase
            pg_conn = cr._cnx
            phase = "pg_is_in_recovery"
            cr.execute("SELECT pg_is_in_recovery()")
            in_recovery = cr.fetchone()[0]
            if not in_recovery:
                phase = "listen_cron_trigger"
                cr.execute("LISTEN cron_trigger")
            else:
                _logger.warning("PG cluster in recovery mode, cron trigger not activated")
            cr.commit()
            alive_time = time.monotonic()
            while (
                odoo_server.config["limit_time_worker_cron"] <= 0
                or (time.monotonic() - alive_time) <= odoo_server.config["limit_time_worker_cron"]
            ):
                phase = "select_wait"
                select.select([pg_conn], [], [], odoo_server.SLEEP_INTERVAL + number)
                time.sleep(number / 100)
                phase = "pg_poll"
                pg_conn.poll()

                registries = odoo.modules.registry.Registry.registries
                _logger.debug("cron%d polling for jobs", number)
                for db_name, registry in registries.d.items():
                    if not registry.ready:
                        continue
                    thread = threading.current_thread()
                    thread.start_time = time.time()
                    try:
                        phase = f"process_jobs:{db_name}"
                        ir_cron._process_jobs(db_name)
                    except Exception as exc:  # noqa: BLE001 - tracing should not mask failures
                        _write_trace_payload({
                            "event": "automotive_cron_job_exception",
                            "cron_number": number,
                            "phase": phase,
                            "db": db_name,
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                            "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                        })
                        _logger.warning("cron%d encountered an Exception:", number, exc_info=True)
                    finally:
                        thread.start_time = None

        while True:
            conn = None
            try:
                phase = "db_connect_postgres"
                conn = odoo.sql_db.db_connect("postgres")
                phase = "cursor_open"
                with contextlib.closing(conn.cursor()) as cr:
                    _run_cron(cr)
                    phase = "connection_close"
                    cr._cnx.close()
                _logger.info(
                    "cron%d max age (%ss) reached, releasing connection.",
                    number,
                    odoo_server.config["limit_time_worker_cron"],
                )
            except Exception as exc:  # noqa: BLE001 - tracing should not mask failures
                _write_trace_payload({
                    "event": "automotive_cron_thread_exception",
                    "cron_number": number,
                    "phase": phase,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                })
                raise
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

    server_cls.cron_thread = traced_cron_thread
    setattr(server_cls, _PATCH_FLAG, True)


if _trace_enabled():
    _patch_cron_thread()
