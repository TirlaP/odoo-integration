# -*- coding: utf-8 -*-
import re

from .invoice_ingest_shared import (
    INVOICE_CODE_STOP_WORDS,
    INVOICE_META_PREFIXES,
    INVOICE_SUPPLIER_NOISE_TOKENS,
    INVOICE_TRIM_SUFFIXES,
    PROGRESSIVE_TRIM_MAX_STEPS,
    PROGRESSIVE_TRIM_MIN_LEN,
    PROGRESSIVE_TRIM_SUPPLIER_TOKENS,
)


def normalize_code_value(value):
    raw = (value or '').strip().upper()
    if not raw:
        return ''
    raw = (
        raw.replace('–', '-')
        .replace('—', '-')
        .replace('−', '-')
    )
    return ' '.join(raw.split())


def compact_code(value):
    return re.sub(r'[^A-Z0-9]', '', normalize_code_value(value))


def is_supplier_token(token):
    token = normalize_code_value(token)
    compact = compact_code(token)
    if not compact:
        return False
    if len(compact) > 15:
        return False
    if compact in INVOICE_SUPPLIER_NOISE_TOKENS:
        return False
    if compact.isdigit():
        return False
    letters = sum(1 for ch in compact if ch.isalpha())
    if letters < 2:
        return False
    return True


def extract_supplier_brand(raw_text, supplier_hint=None):
    if supplier_hint and is_supplier_token(supplier_hint):
        return compact_code(supplier_hint)

    text = normalize_code_value(raw_text)
    if not text:
        return ''

    parts = [part.strip() for part in re.split(r'\s+-\s+', text) if part.strip()]
    for part in reversed(parts):
        if is_supplier_token(part):
            return compact_code(part)

    tokens = text.split()
    for token in reversed(tokens):
        if is_supplier_token(token):
            return compact_code(token)
    return ''


def extract_primary_code(raw_text):
    text = normalize_code_value(raw_text)
    if not text:
        return ''

    parts = [part.strip() for part in re.split(r'\s+-\s+', text) if part.strip()]
    if len(parts) >= 2:
        return extract_primary_code(parts[0])

    tokens = [tok.strip(",.;:()[]") for tok in text.split() if tok.strip(",.;:()[]")]
    if not tokens:
        return ''

    first = re.sub(r'[^A-Z0-9-]', '', normalize_code_value(tokens[0]))
    if not first:
        return ''

    selected = [first]
    if compact_code(first) in INVOICE_CODE_STOP_WORDS:
        return ''

    for token in tokens[1:4]:
        normalized = normalize_code_value(token)
        candidate = re.sub(r'[^A-Z0-9-]', '', normalized)
        if not candidate:
            break
        if compact_code(candidate) in INVOICE_CODE_STOP_WORDS:
            break

        first_compact = compact_code(selected[0])
        first_is_short_alpha = first_compact.isalpha() and len(first_compact) <= 5
        if first_is_short_alpha and candidate.isdigit() and len(candidate) <= 4:
            selected.append(candidate)
            continue
        if len(selected) >= 2 and selected[1].isdigit() and candidate.isdigit() and len(candidate) <= 4:
            selected.append(candidate)
            continue
        break

    return ' '.join(selected)


def trimmed_code_variants(code):
    compact = compact_code(code)
    variants = []

    for suffix in INVOICE_TRIM_SUFFIXES:
        if compact.endswith(suffix) and len(compact) > len(suffix) + 3:
            candidate = compact[: -len(suffix)]
            if candidate and candidate not in variants:
                variants.append(candidate)
    return variants


def prefix_stripped_code_variants(code):
    compact = compact_code(code)
    if not compact:
        return []

    match = re.match(r'^([A-Z]{2,5})(\d[A-Z0-9]{3,})$', compact)
    if not match:
        return []

    prefix, remainder = match.groups()
    if not prefix or not remainder or not re.search(r'\d', remainder):
        return []
    return [remainder]


def progressive_tail_trim_candidates(code):
    compact = compact_code(code)
    if not compact:
        return []
    if not re.search(r'[A-Z]$', compact):
        return []
    if not re.search(r'\d', compact):
        return []

    candidates = []
    current = compact
    steps = 0
    while (
        current
        and current[-1].isalpha()
        and len(current) > PROGRESSIVE_TRIM_MIN_LEN
        and steps < PROGRESSIVE_TRIM_MAX_STEPS
    ):
        current = current[:-1]
        steps += 1
        if current and current not in candidates:
            candidates.append(current)
    return candidates


def allow_progressive_tail_trim_name(supplier_name=''):
    normalized_name = normalize_code_value(supplier_name or '')
    return any(token in normalized_name for token in PROGRESSIVE_TRIM_SUPPLIER_TOKENS)


def build_openai_extraction_prompt(supplier_name_hint=''):
    prompt = (
        "Extract invoice data from Romanian automotive supplier invoice text. "
        "Return strict JSON with keys: "
        "supplier_name, supplier_code, invoice_number, invoice_date, invoice_due_date, "
        "invoice_currency, vat_rate, amount_total, confidence, warnings, document_type, invoice_lines. "
        "supplier_name must be the invoice issuer/vendor from the Furnizor or supplier section, never the client/customer. "
        "invoice_number must be the exact invoice number shown on the document header. "
        "document_type must be one of invoice, credit_note, refund, or unknown when the document is clearly a supplier credit note or refund. "
        "invoice_lines is an array of objects with: "
        "quantity, product_code, product_code_raw, supplier_brand, product_description, unit_price. "
        "product_code_raw must preserve the exact printed article code from the document. "
        "For normal suppliers, product_code must also preserve the full printed article code; do not remove trailing letters or suffixes. "
        "If the printed code looks like C2W029ABE, keep C2W029ABE as the code; do not split it into C2W029 and ABE. "
        "supplier_brand should contain only the supplier brand token (e.g. TRW, BOSCH, SKF). "
        "Exclude NC= and CPV= values from product_code. "
        "Use ISO date format YYYY-MM-DD. If unknown, use null. confidence must be 0..100."
    )
    if allow_progressive_tail_trim_name(supplier_name_hint):
        prompt += (
            " Special case for Auto Total invoices: keep the exact printed value in product_code_raw, "
            "but product_code may contain the trimmed main article code when supplier suffix letters are glued "
            "to the end of the printed code."
        )
    return prompt


def code_candidates(value, extra=None):
    candidates = []

    def _add(raw_value):
        normalized = normalize_code_value(raw_value)
        if not normalized:
            return
        for candidate in (
            normalized,
            re.sub(r'\s*-\s*', '-', normalized),
            normalized.replace(' ', ''),
            normalized.replace('-', ''),
            re.sub(r'[^A-Z0-9]', '', normalized),
        ):
            if candidate and candidate not in candidates:
                candidates.append(candidate)

    _add(value)
    for raw in (extra or []):
        _add(raw)
    return candidates


def parse_invoice_line_identity(product_code_raw, product_description='', supplier_hint=''):
    raw_code = normalize_code_value(product_code_raw)
    raw_description = normalize_code_value(product_description)

    combined = f'{raw_code} {raw_description}'.strip()
    for marker in INVOICE_META_PREFIXES:
        combined = re.sub(rf'\b{marker}\s*[^\s]+', ' ', combined, flags=re.IGNORECASE)
    combined = ' '.join(combined.split())

    primary = extract_primary_code(raw_code or combined)
    if not primary:
        primary = extract_primary_code(combined)

    supplier_brand = extract_supplier_brand(raw_code or combined, supplier_hint=supplier_hint)
    parsed = {
        'product_code_raw': raw_code or product_code_raw or '',
        'product_code_primary': primary or '',
        'product_code_compact': compact_code(primary),
        'supplier_brand': supplier_brand,
        'code_candidates': [],
    }
    parsed['code_candidates'] = code_candidates(
        parsed['product_code_primary'],
        extra=trimmed_code_variants(parsed['product_code_primary']),
    )
    return parsed
