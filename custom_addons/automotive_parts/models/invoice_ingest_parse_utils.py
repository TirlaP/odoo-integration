# -*- coding: utf-8 -*-
import os
import re
from datetime import datetime

from odoo import fields


def safe_money(value, default=0.0):
    if value in (None, False, ''):
        return default
    raw = str(value).strip().replace(' ', '')
    if not raw:
        return default
    try:
        if ',' in raw and '.' in raw:
            if raw.rfind(',') > raw.rfind('.'):
                raw = raw.replace('.', '').replace(',', '.')
            else:
                raw = raw.replace(',', '')
        elif ',' in raw:
            right = raw.split(',')[-1]
            if right.isdigit() and len(right) == 2:
                raw = raw.replace(',', '.')
            else:
                raw = raw.replace(',', '')
        return float(raw)
    except Exception:
        return default


def safe_float(value, default=0.0):
    if value in (None, False, ''):
        return default
    try:
        return float(str(value).replace(',', '.'))
    except Exception:
        return default


def safe_date(value):
    if not value:
        return False
    if isinstance(value, datetime):
        return value.date()
    raw = str(value).strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d/%m/%y', '%d.%m.%Y', '%d.%m.%y'):
        try:
            return datetime.strptime(raw, fmt).date()
        except Exception:
            continue
    try:
        return fields.Date.to_date(raw)
    except Exception:
        return False


def extract_invoice_totals_from_text(text):
    if not text:
        return {}

    out = {}
    vat_match = re.search(r'Cota\s*T\.V\.A\.\s*:?\s*([0-9]+(?:[.,][0-9]+)?)\s*%', text, re.IGNORECASE)
    if vat_match:
        out['vat_rate'] = safe_money(vat_match.group(1), default=0.0)

    semn_matches = list(
        re.finditer(
            r'Semnaturile\s+([0-9]{1,3}(?:[.,][0-9]{3})*[.,][0-9]{2})\s+([0-9]{1,3}(?:[.,][0-9]{3})*[.,][0-9]{2})',
            text,
            re.IGNORECASE,
        )
    )
    if semn_matches:
        last = semn_matches[-1]
        out['total_excl_vat'] = safe_money(last.group(1), default=0.0)
        out['vat_amount'] = safe_money(last.group(2), default=0.0)

    plata_matches = list(
        re.finditer(
            r'Total\s+de\s+plata[\s\S]{0,80}?([0-9]{1,3}(?:[.,][0-9]{3})*[.,][0-9]{2})',
            text,
            re.IGNORECASE,
        )
    )
    if plata_matches:
        out['amount_total'] = safe_money(plata_matches[-1].group(1), default=0.0)
    elif out.get('total_excl_vat') or out.get('vat_amount'):
        out['amount_total'] = (out.get('total_excl_vat') or 0.0) + (out.get('vat_amount') or 0.0)

    return out


def extract_invoice_number_from_filename(filename):
    stem = os.path.splitext(os.path.basename(filename or ''))[0]
    compact = re.sub(r'[^A-Z0-9]', '', stem.upper())
    if compact and sum(ch.isdigit() for ch in compact) >= 4:
        return compact
    return ''


def extract_invoice_header_from_text(text, filename=None):
    if not text and not filename:
        return {}

    out = {}
    raw_lines = [line.rstrip() for line in (text or '').splitlines()]
    non_empty_lines = [line.strip() for line in raw_lines if line and line.strip()]

    for idx, line in enumerate(non_empty_lines):
        if re.search(r'\bFurnizor\b', line, re.IGNORECASE):
            same_line = re.search(r'\bFurnizor\s*:?\s*(.+?)(?:\s{2,}|$)', line, re.IGNORECASE)
            if same_line:
                supplier_name = same_line.group(1).strip()
                if supplier_name:
                    out['supplier_name'] = supplier_name
                    break
            for candidate in non_empty_lines[idx + 1:idx + 5]:
                parts = [part.strip() for part in re.split(r'\s{2,}', candidate) if part.strip()]
                if parts:
                    out['supplier_name'] = parts[0]
                    break
            if out.get('supplier_name'):
                break

    for line in non_empty_lines:
        if 'C.I.F.' not in line.upper():
            continue
        parts = [part.strip() for part in re.split(r'\s{2,}', line) if part.strip()]
        vat = next((part for part in parts if re.fullmatch(r'RO?\d{2,}', part, re.IGNORECASE)), '')
        if vat:
            out['supplier_vat'] = vat
            break

    dates = re.findall(r'\b\d{2}[./-]\d{2}[./-]\d{2,4}\b', text or '')
    if dates:
        out['invoice_date'] = dates[0]
        if len(dates) > 1:
            out['invoice_due_date'] = dates[-1]

    invoice_number = extract_invoice_number_from_filename(filename)
    if not invoice_number:
        scan_area = ''.join(non_empty_lines[:4]).upper()
        scan_area = re.sub(r'[^A-Z0-9]', '', scan_area)
        match = re.search(r'(RO\d{6,}|[A-Z]{1,4}\d{6,})', scan_area)
        if match:
            invoice_number = match.group(1)
    if invoice_number:
        out['invoice_number'] = invoice_number

    return out


def normalize_cui_digits(value):
    if not value:
        return ''
    return ''.join(ch for ch in str(value) if ch.isdigit())
