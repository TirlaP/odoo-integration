# -*- coding: utf-8 -*-


def _table_exists(cr, table):
    cr.execute("SELECT to_regclass(%s)", (table,))
    return bool(cr.fetchone()[0])


def _column_exists(cr, table, column):
    cr.execute(
        """
        SELECT 1
          FROM information_schema.columns
         WHERE table_name = %s
           AND column_name = %s
        """,
        (table, column),
    )
    return bool(cr.fetchone())


def migrate(cr, version):
    if not _table_exists(cr, "automotive_runtime_log"):
        return
    if not _table_exists(cr, "automotive_audit_log"):
        return
    if not _column_exists(cr, "automotive_audit_log", "legacy_runtime_log_id"):
        return

    cr.execute(
        """
        UPDATE automotive_audit_log
           SET log_type = 'audit'
         WHERE log_type IS NULL
        """
    )

    cr.execute(
        """
        INSERT INTO automotive_audit_log (
            create_uid,
            create_date,
            write_uid,
            write_date,
            user_id,
            company_id,
            action,
            log_type,
            model_name,
            model_description,
            record_id,
            record_display_name,
            description,
            level,
            category,
            event,
            source,
            outcome,
            db_name,
            request_method,
            request_path,
            related_model,
            related_res_id,
            message,
            payload_json,
            legacy_runtime_log_id
        )
        SELECT
            COALESCE(rtl.create_uid, 1),
            rtl.create_date,
            COALESCE(rtl.write_uid, rtl.create_uid, 1),
            rtl.write_date,
            COALESCE(rtl.user_id, 1),
            NULL,
            'custom',
            'runtime',
            COALESCE(NULLIF(rtl.related_model, ''), 'automotive.runtime.log'),
            COALESCE(NULLIF(rtl.related_model, ''), 'Runtime Log'),
            rtl.related_res_id,
            COALESCE(NULLIF(rtl.event, ''), 'runtime_event'),
            COALESCE(rtl.message, ''),
            rtl.level,
            rtl.category,
            COALESCE(NULLIF(rtl.event, ''), 'runtime_event'),
            rtl.source,
            rtl.outcome,
            rtl.db_name,
            rtl.request_method,
            rtl.request_path,
            rtl.related_model,
            rtl.related_res_id,
            rtl.message,
            rtl.payload_json,
            rtl.id
          FROM automotive_runtime_log rtl
         WHERE NOT EXISTS (
            SELECT 1
              FROM automotive_audit_log aal
             WHERE aal.log_type = 'runtime'
               AND aal.legacy_runtime_log_id = rtl.id
         )
        """
    )
