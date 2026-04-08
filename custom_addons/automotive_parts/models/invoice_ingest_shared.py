# -*- coding: utf-8 -*-
import logging
import re

from odoo import models


INVOICE_META_PREFIXES = ('NC=', 'CPV=')
INVOICE_SUPPLIER_NOISE_TOKENS = {
    'OEM', 'AM', 'OE', 'OES', 'AFTERMARKET',
    'BC', 'BUC', 'PCS', 'SET', 'PIECE', 'PIESE',
    'NC', 'CPV',
}
INVOICE_CODE_STOP_WORDS = {
    'SET', 'FILTRU', 'CUREA', 'BECURI', 'INTINZATOR', 'STERGATOR',
    'TERMOSTAT', 'BLISTER', 'DE', 'CU', 'SI', 'LA', 'PENTRU', 'TIP',
}
INVOICE_TRIM_SUFFIXES = ('CT', 'V')
AUTO_MATCH_CONFIDENCE_THRESHOLD = 88.0
PROGRESSIVE_TRIM_MIN_LEN = 5
PROGRESSIVE_TRIM_MAX_STEPS = 8
PROGRESSIVE_TRIM_SUPPLIER_TOKENS = ('AUTO TOTAL',)
INVOICE_INGEST_ASYNC_JOB_TYPE = 'invoice_ingest'
INVOICE_INGEST_ASYNC_TARGET_MODEL = 'invoice.ingest.job'
INVOICE_INGEST_ASYNC_TARGET_METHOD = '_process_ingest_job'
INVOICE_INGEST_ASYNC_QUEUED_MESSAGE = 'Queued, waiting for worker'

_logger = logging.getLogger(__name__)


def snapshot_record(record, field_names=None):
    record.ensure_one()
    tracked_fields = field_names or set()
    snapshot = {}
    for field_name in tracked_fields:
        if field_name not in record._fields:
            continue
        value = record[field_name]
        if isinstance(value, models.BaseModel):
            snapshot[field_name] = value.ids
        else:
            snapshot[field_name] = value
    return snapshot


def normalize_invoice_number(invoice_number):
    return ' '.join((invoice_number or '').split())


def normalize_invoice_number_key(invoice_number):
    return re.sub(r'[^A-Z0-9]+', '', normalize_invoice_number(invoice_number).upper())

