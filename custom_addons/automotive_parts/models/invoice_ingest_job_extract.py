# -*- coding: utf-8 -*-
import base64
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
from io import BytesIO

import requests
from PyPDF2 import PdfReader
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .invoice_ingest_document_utils import (
    detect_attachment_kind,
    infer_document_move_type_from_xml,
    looks_like_supplier_credit_note_text,
    ocr_image_path,
    prepare_ocr_image_path,
)
from .invoice_ingest_parse_utils import (
    extract_invoice_header_from_text,
    extract_invoice_number_from_filename,
    extract_invoice_totals_from_text,
    safe_date,
    safe_float,
    safe_money,
)
from .invoice_ingest_shared import _logger


class InvoiceIngestJobExtract(models.Model):
    _inherit = 'invoice.ingest.job'
    @api.model
    def _infer_document_move_type_from_xml(self, xml_payload):
        return infer_document_move_type_from_xml(xml_payload)

    @api.model
    def _looks_like_supplier_credit_note_text(self, text):
        return looks_like_supplier_credit_note_text(text)

    def _infer_vendor_bill_move_type(self, payload=None, text_hint=None):
        self.ensure_one()
        payload = payload if isinstance(payload, dict) else self._get_payload_dict()

        normalized = (payload.get('openai') or {}).get('normalized') or {}
        document_type = (normalized.get('document_type') or normalized.get('invoice_type') or '').strip().lower()
        if document_type in {'creditnote', 'credit_note', 'refund', 'supplier_credit_note', 'supplier_refund'}:
            return 'in_refund'
        if document_type in {'invoice', 'bill', 'supplier_invoice'}:
            return 'in_invoice'

        if self.document_type == 'credit_note':
            return 'in_refund'
        if self.document_type == 'invoice':
            return 'in_invoice'

        raw_openai = (payload.get('openai') or {}).get('raw') or {}
        if isinstance(raw_openai, dict):
            document_type = (
                raw_openai.get('document_type')
                or raw_openai.get('invoice_type')
                or raw_openai.get('invoiceTypeCode')
                or ''
            )
            if isinstance(document_type, str):
                normalized_type = document_type.strip().lower()
                if normalized_type in {'creditnote', 'credit_note', 'refund'}:
                    return 'in_refund'
                if normalized_type in {'invoice', 'bill'}:
                    return 'in_invoice'

        raw_payload = payload.get('raw')
        if isinstance(raw_payload, dict):
            xml_payload = (
                raw_payload.get('xml')
                or raw_payload.get('ubl_xml')
                or raw_payload.get('document_xml')
                or raw_payload.get('parsed', {}).get('xml_payload')
                or raw_payload.get('parsed', {}).get('xml')
            )
            inferred = self._infer_document_move_type_from_xml(xml_payload)
            if inferred:
                return inferred

        if text_hint and self._looks_like_supplier_credit_note_text(text_hint):
            return 'in_refund'

        # OCR fallback: inspect the extracted text cached in the payload if available.
        if self._looks_like_supplier_credit_note_text(json.dumps(payload, ensure_ascii=False, default=str)):
            return 'in_refund'

        return 'in_invoice'

    @api.model
    def _detect_attachment_kind(self, binary, filename=None, mimetype=None):
        return detect_attachment_kind(binary, filename=filename, mimetype=mimetype)

    @api.model
    def _prepare_ocr_image_path(self, image):
        return prepare_ocr_image_path(image)

    @api.model
    def _ocr_image_path(self, image_path):
        return ocr_image_path(image_path)

    @api.model
    def _extract_image_text_with_ocr(self, binary):
        if not binary:
            return ''
        try:
            from PIL import Image, ImageSequence
        except Exception:
            return ''
        if not shutil.which('tesseract'):
            return ''

        temp_paths = []
        texts = []
        try:
            with Image.open(BytesIO(binary)) as image:
                frame_count = getattr(image, 'n_frames', 1) or 1
                frames = ImageSequence.Iterator(image) if frame_count > 1 else [image]
                for frame in frames:
                    processed_path = self._prepare_ocr_image_path(frame.copy())
                    if not processed_path:
                        continue
                    temp_paths.append(processed_path)
                    ocr_text = self._ocr_image_path(processed_path)
                    if ocr_text:
                        texts.append(ocr_text)
        except Exception:
            return ''
        finally:
            for path in temp_paths:
                try:
                    os.unlink(path)
                except Exception:
                    pass
        return '\n'.join(texts).strip()

    @api.model
    def _extract_pdf_text_with_ocr(self, binary):
        if not binary or not shutil.which('pdftoppm') or not shutil.which('tesseract'):
            return ''
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                pdf_path = os.path.join(temp_dir, 'invoice.pdf')
                prefix = os.path.join(temp_dir, 'page')
                with open(pdf_path, 'wb') as handle:
                    handle.write(binary)
                result = subprocess.run(
                    ['pdftoppm', '-png', '-r', '300', pdf_path, prefix],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                if result.returncode != 0:
                    return ''
                texts = []
                for image_path in sorted(glob.glob(f'{prefix}-*.png')):
                    ocr_text = self._ocr_image_path(image_path)
                    if ocr_text:
                        texts.append(ocr_text)
                return '\n'.join(texts).strip()
        except Exception:
            return ''

    def _get_attachment_binary(self, raise_if_missing=True):
        self.ensure_one()
        if self.attachment_data:
            return base64.b64decode(self.attachment_data)

        attachment = self.attachment_id.sudo()
        if not attachment:
            if raise_if_missing:
                raise UserError('Attach a PDF or image first.')
            return b''

        try:
            raw = attachment.raw
        except Exception:
            raw = b''
        if raw:
            return raw

        datas = attachment.datas
        if datas:
            return base64.b64decode(datas)

        if raise_if_missing:
            raise UserError(
                'The attached PDF/image file is no longer available on the server. Re-upload the document and try again.'
            )
        return b''

    def _extract_pdf_text(self):
        self.ensure_one()
        if not self.attachment_id and not self.attachment_data:
            raise UserError('Attach a PDF or image first.')

        self._report_async_progress(10.0, _('Reading attachment'))
        binary = self._get_attachment_binary()
        kind = self._detect_attachment_kind(
            binary,
            filename=self.attachment_filename or self.attachment_id.name,
            mimetype=self.attachment_id.mimetype if self.attachment_id else None,
        )
        if kind == 'image':
            self._report_async_progress(25.0, _('Preparing image OCR'))
            self._report_async_progress(40.0, _('Running OCR on image'))
            return self._extract_image_text_with_ocr(binary)
        if kind != 'pdf':
            raise UserError('Unsupported attachment type. Upload a PDF or image first.')

        self._report_async_progress(25.0, _('Extracting PDF text'))
        layout_text = self._extract_pdf_text_with_pdftotext(binary)
        if layout_text and len(layout_text) >= 20:
            return layout_text

        text = ''
        try:
            reader = PdfReader(BytesIO(binary))
            pages = []
            for page in reader.pages:
                try:
                    pages.append(page.extract_text() or '')
                except Exception:
                    continue
            text = '\n'.join(pages).strip()
        except Exception:
            text = ''
        if text and len(text) >= 20:
            return text

        self._report_async_progress(40.0, _('Running OCR fallback'))
        ocr_text = self._extract_pdf_text_with_ocr(binary)
        if ocr_text:
            return ocr_text

        return text

    @api.model
    def _extract_pdf_text_with_pdftotext(self, binary):
        if not binary or not shutil.which('pdftotext'):
            return ''
        try:
            with tempfile.NamedTemporaryFile(suffix='.pdf') as tmp:
                tmp.write(binary)
                tmp.flush()
                result = subprocess.run(
                    ['pdftotext', '-layout', tmp.name, '-'],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
        except Exception:
            return ''
        if result.returncode != 0:
            return ''
        return (result.stdout or '').strip()

    @api.model
    def _safe_money(self, value, default=0.0):
        return safe_money(value, default=default)

    @api.model
    def _extract_invoice_totals_from_text(self, text):
        return extract_invoice_totals_from_text(text)

    @api.model
    def _extract_invoice_lines_from_text(self, text, default_vat_rate=0.0):
        if not text:
            return []

        row_re = re.compile(
            r'^\s*(\d{1,3})\s+(.+?)\s+([A-Z]{2,6})\s+'
            r'([0-9]+(?:[.,][0-9]+)?)\s+'
            r'([0-9]{1,3}(?:[.,][0-9]{3})*[.,][0-9]{2})\s+'
            r'([0-9]{1,3}(?:[.,][0-9]{3})*[.,][0-9]{2})\s+'
            r'([0-9]{1,3}(?:[.,][0-9]{3})*[.,][0-9]{2})\s*$'
        )
        footer_re = re.compile(
            r'^(Aceasta factura|Data sc:|In cazul in care plata|Orice litigiu|Semnaturile|Total\b|din care:|Expedierea s-a efectuat)',
            re.IGNORECASE,
        )

        rows = []
        current = None
        for raw_line in text.splitlines():
            line = (raw_line or '').strip()
            if not line:
                continue

            row_match = row_re.match(line)
            if row_match:
                if current:
                    rows.append(current)
                current = {
                    'sequence': int(row_match.group(1)),
                    'quantity': self._safe_money(row_match.group(4), default=1.0) or 1.0,
                    'unit_price': self._safe_money(row_match.group(5), default=0.0),
                    'line_total_excl_vat': self._safe_money(row_match.group(6), default=0.0),
                    'line_vat_amount': self._safe_money(row_match.group(7), default=0.0),
                    'desc_parts': [row_match.group(2).strip()],
                }
                continue

            if not current:
                continue
            if footer_re.match(line):
                rows.append(current)
                current = None
                continue
            if line.startswith('NC=') or line.startswith('CPV='):
                continue
            current['desc_parts'].append(line)

        if current:
            rows.append(current)

        by_sequence = {}
        for row in rows:
            seq = row.get('sequence') or 0
            if seq <= 0:
                continue
            by_sequence[seq] = row

        out = []
        for seq in sorted(by_sequence.keys()):
            row = by_sequence[seq]
            description = ' '.join(p for p in (row.get('desc_parts') or []) if p).strip()
            if not description:
                continue
            parsed = self._parse_invoice_line_identity(description)
            line_total = row.get('line_total_excl_vat') or 0.0
            vat_amount = row.get('line_vat_amount') or 0.0
            vat_rate = default_vat_rate or 0.0
            if line_total > 0 and vat_amount >= 0:
                inferred = round((vat_amount / line_total) * 100.0, 2)
                if inferred > 0:
                    vat_rate = inferred
            out.append(self._build_normalized_invoice_line(
                quantity=row.get('quantity') or 1.0,
                product_description=description,
                unit_price=row.get('unit_price') or 0.0,
                vat_rate=vat_rate,
                resolved={
                    'product_code_raw': parsed.get('product_code_raw') or description,
                    'product_code': self._compact_code(parsed.get('product_code_primary')) or False,
                    'supplier_brand': parsed.get('supplier_brand') or '',
                },
            ))
        return out

    @api.model
    def _merge_fallback_line_codes(self, ai_lines, fallback_lines):
        if not ai_lines or not fallback_lines or len(ai_lines) != len(fallback_lines):
            return ai_lines or [], 0

        merged_lines = []
        recovered_count = 0
        for ai_line, fallback_line in zip(ai_lines, fallback_lines):
            if not isinstance(ai_line, dict) or not isinstance(fallback_line, dict):
                merged_lines.append(ai_line)
                continue

            merged_line = dict(ai_line)
            ai_code = self._compact_code(
                merged_line.get('product_code_raw')
                or merged_line.get('product_code')
            )
            fallback_code = self._compact_code(
                fallback_line.get('product_code_raw')
                or fallback_line.get('product_code')
            )
            use_fallback_code = (
                bool(fallback_code)
                and (
                    not ai_code
                    or (len(fallback_code) > len(ai_code) and fallback_code.startswith(ai_code))
                )
            )
            if use_fallback_code:
                merged_line['product_code_raw'] = (
                    fallback_line.get('product_code_raw')
                    or fallback_line.get('product_code')
                    or merged_line.get('product_code_raw')
                )
                merged_line['product_code'] = (
                    fallback_line.get('product_code')
                    or fallback_line.get('product_code_raw')
                    or merged_line.get('product_code')
                )
                if not merged_line.get('supplier_brand') and fallback_line.get('supplier_brand'):
                    merged_line['supplier_brand'] = fallback_line.get('supplier_brand')
                recovered_count += 1
            merged_lines.append(merged_line)
        return merged_lines, recovered_count

    @api.model
    def _safe_float(self, value, default=0.0):
        return safe_float(value, default=default)

    @api.model
    def _safe_date(self, value):
        return safe_date(value)

    @api.model
    def _missing_runtime_schema_items(self):
        checks = [
            ('invoice_product_code_map', None),
            ('tecdoc_article_variant', 'identity_key'),
            ('tecdoc_article_variant', 'is_reference_only'),
            ('tecdoc_article_variant', 'last_enriched_at'),
        ]
        missing = []
        for table, column in checks:
            self.env.cr.execute('SELECT to_regclass(%s)', (table,))
            row = self.env.cr.fetchone()
            if not row or not row[0]:
                missing.append(table)
                continue
            if not column:
                continue
            self.env.cr.execute(
                """
                SELECT 1
                  FROM information_schema.columns
                 WHERE table_schema = current_schema()
                   AND table_name = %s
                   AND column_name = %s
                 LIMIT 1
                """,
                (table, column),
            )
            if not self.env.cr.fetchone():
                missing.append(f'{table}.{column}')
        return missing

    @api.model
    def _ensure_runtime_schema_ready(self):
        missing = self._missing_runtime_schema_items()
        if missing:
            raise UserError(
                _(
                    'Database schema is not upgraded for automotive_parts. '
                    'Missing: %(missing)s. Run module upgrade (-u automotive_parts) before processing invoices.'
                ) % {'missing': ', '.join(missing)}
            )
        return True

    @api.model
    def _should_use_pdf_parser_without_openai(self, pdf_header, pdf_totals, fallback_lines):
        return bool(
            fallback_lines
            and len(fallback_lines) >= 3
            and self._safe_float(pdf_totals.get('amount_total')) > 0.0
            and pdf_header.get('invoice_number')
            and pdf_header.get('supplier_name')
        )

    @api.model
    def _extract_invoice_number_from_filename(self, filename):
        return extract_invoice_number_from_filename(filename)

    @api.model
    def _extract_invoice_header_from_text(self, text, filename=None):
        return extract_invoice_header_from_text(text, filename=filename)

    def _process_ingest_job(self, raise_on_error=False):
        for job in self:
            if job.state not in {'pending', 'running', 'failed', 'needs_review'}:
                continue
            try:
                job._ensure_async_not_cancelled()
                raise_on_error = raise_on_error or bool(self.env.context.get('skip_automotive_async_queue'))
                start_values = job._build_running_state_values()
                if start_values:
                    job.write(start_values)
                job._ensure_async_not_cancelled()
                if job.source == 'ocr' and job.attachment_id:
                    job.action_extract_with_openai()
                elif job.account_move_id:
                    job.write(job._build_finished_state_values('done'))
                else:
                    job.write(job._build_finished_state_values('needs_review'))
                if job.state in {'done', 'needs_review'} and not job.finished_at:
                    job.write({'finished_at': fields.Datetime.now()})
            except Exception as exc:  # noqa: BLE001
                error_message = str(exc) or repr(exc)
                # SQL/ORM failures leave the cursor unusable until rollback. If we
                # write failure state first, PostgreSQL masks the original error
                # with "current transaction is aborted".
                self.env.cr.rollback()
                latest = self.browse(job.id).exists()
                async_job = latest._get_context_async_job() if latest else self.env['automotive.async.job']
                if async_job and self.env['automotive.async.job'].is_cancel_requested(async_job.id):
                    if latest:
                        latest.with_context(skip_audit_log=True).write({
                            'error': False,
                            'finished_at': fields.Datetime.now(),
                        })
                    continue
                if latest:
                    try:
                        latest.write(latest._build_failed_state_values(error_message))
                    except Exception:
                        _logger.exception(
                            'Failed to persist invoice ingest failure state for job %s',
                            latest.id,
                        )
                if raise_on_error:
                    raise
        return True

    def action_extract_with_openai(self):
        self._ensure_runtime_schema_ready()

        for job in self:
            job._ensure_async_not_cancelled()
            text = job._extract_pdf_text()
            if not text or len(text) < 20:
                raise UserError(
                    'Document text extraction returned no usable text. Use ANAF XML import or install Tesseract OCR for scanned documents.'
                )
            job._ensure_async_not_cancelled()
            pdf_totals = job._extract_invoice_totals_from_text(text)
            pdf_header = job._extract_invoice_header_from_text(
                text,
                filename=job.attachment_id.name if job.attachment_id else job.name,
            )
            fallback_lines = job._extract_invoice_lines_from_text(
                text,
                default_vat_rate=pdf_totals.get('vat_rate') or 0.0,
            )

            prompt = job._build_openai_extraction_prompt(
                supplier_name_hint=pdf_header.get('supplier_name') or '',
            )
            api_key = job._get_openai_api_key()
            parsed = {}
            openai_error = False
            parsed_source = False
            openai_attempted = False
            if job._should_use_pdf_parser_without_openai(pdf_header, pdf_totals, fallback_lines):
                parsed_source = 'pdf_parser_skipped_openai'
                parsed = {
                    'supplier_name': pdf_header.get('supplier_name') or '',
                    'invoice_number': pdf_header.get('invoice_number') or '',
                    'invoice_date': pdf_header.get('invoice_date') or False,
                    'invoice_due_date': pdf_header.get('invoice_due_date') or False,
                    'invoice_currency': self.env.company.currency_id.name,
                    'vat_rate': pdf_totals.get('vat_rate') or 0.0,
                    'amount_total': pdf_totals.get('amount_total') or 0.0,
                    'confidence': 0.0,
                    'invoice_lines': fallback_lines,
                    'warnings': [
                        'Used deterministic PDF text parser; OpenAI extraction was skipped.',
                    ],
                }
            elif api_key:
                openai_attempted = True
                job._report_async_progress(60.0, _('Calling OpenAI extraction'))
                body = {
                    'model': job.ai_model or job._default_ai_model(),
                    'response_format': {'type': 'json_object'},
                    'messages': [
                        {'role': 'system', 'content': 'You are a strict invoice extraction engine. Output valid JSON only.'},
                        {
                            'role': 'user',
                            'content': (
                                f'{prompt}\n\n'
                                f'FILENAME: {(job.attachment_id.name if job.attachment_id else job.name) or ""}\n\n'
                                f'INVOICE_TEXT:\n{text[:120000]}'
                            ),
                        },
                    ],
                }
                try:
                    response = requests.post(
                        'https://api.openai.com/v1/chat/completions',
                        headers={
                            'Authorization': f'Bearer {api_key}',
                            'Content-Type': 'application/json',
                        },
                        json=body,
                        timeout=120,
                    )
                    if response.status_code >= 400:
                        raise UserError(f'OpenAI extraction failed: {response.text}')
                    result = response.json()
                    content = (
                        result.get('choices', [{}])[0]
                        .get('message', {})
                        .get('content')
                    )
                    if not content:
                        raise UserError('OpenAI returned empty content.')
                    parsed = json.loads(content)
                    if not isinstance(parsed, dict):
                        raise UserError('OpenAI response is not a JSON object.')
                    parsed_source = 'openai'
                except (requests.exceptions.RequestException, ValueError, UserError) as exc:
                    openai_error = str(exc) or repr(exc)
                    parsed = {}
                job._ensure_async_not_cancelled()
            else:
                openai_error = (
                    'Missing OPENAI_API_KEY. Set env var OPENAI_API_KEY or config parameter automotive.openai_api_key.'
                )

            parsed_vat_rate = self._safe_float(parsed.get('vat_rate')) if parsed else 0.0
            if parsed and fallback_lines and not pdf_totals.get('vat_rate') and parsed_vat_rate > 0.0:
                fallback_lines = job._extract_invoice_lines_from_text(
                    text,
                    default_vat_rate=parsed_vat_rate,
                )

            if not parsed:
                if not fallback_lines:
                    raise UserError(
                        '%s PDF parser also found no invoice lines.'
                        % (openai_error or 'OpenAI extraction returned no usable data.')
                    )
                parsed_source = 'pdf_parser_after_openai_error' if openai_error else 'pdf_parser'
                parsed = {
                    'supplier_name': pdf_header.get('supplier_name') or '',
                    'invoice_number': pdf_header.get('invoice_number') or '',
                    'invoice_date': pdf_header.get('invoice_date') or False,
                    'invoice_due_date': pdf_header.get('invoice_due_date') or False,
                    'invoice_currency': self.env.company.currency_id.name,
                    'vat_rate': pdf_totals.get('vat_rate') or 0.0,
                    'amount_total': pdf_totals.get('amount_total') or 0.0,
                    'confidence': 0.0,
                    'invoice_lines': fallback_lines,
                    'warnings': [
                        'OpenAI extraction failed; used deterministic PDF text parser lines instead.',
                    ],
                }
                if openai_error:
                    parsed['warnings'].append(openai_error)

            parsed_lines = parsed.get('invoice_lines') or []
            used_pdf_parser_lines = parsed_source != 'openai'
            ai_lines = parsed_lines if parsed_source == 'openai' else []
            lines = fallback_lines if used_pdf_parser_lines else ai_lines
            warnings = parsed.get('warnings') if isinstance(parsed.get('warnings'), list) else []
            supplier_name = (parsed.get('supplier_name') or pdf_header.get('supplier_name') or '').strip()
            invoice_number = self._normalize_invoice_number(
                parsed.get('invoice_number') or pdf_header.get('invoice_number')
            )
            invoice_date_value = parsed.get('invoice_date') or pdf_header.get('invoice_date')
            invoice_due_date_value = parsed.get('invoice_due_date') or pdf_header.get('invoice_due_date')
            currency = self.env.company.currency_id
            currency_name = (parsed.get('invoice_currency') or '').strip().upper()
            if currency_name:
                currency = self.env['res.currency'].search([('name', '=', currency_name)], limit=1) or currency
            if not parsed.get('supplier_name') and supplier_name:
                warnings.append('Supplier name recovered from the PDF header.')
            if not parsed.get('invoice_number') and invoice_number:
                warnings.append(f'Invoice number recovered from the file/header: {invoice_number}.')
            if not parsed.get('invoice_date') and invoice_date_value:
                warnings.append('Invoice date recovered from the PDF header.')
            supplier = job._get_or_create_supplier_partner(
                supplier_name=supplier_name,
                supplier_code=parsed.get('supplier_code'),
                supplier_vat=pdf_header.get('supplier_vat'),
            )
            if ai_lines and fallback_lines and not job._allow_progressive_tail_trim(supplier or supplier_name):
                merged_lines, recovered_code_count = job._merge_fallback_line_codes(ai_lines, fallback_lines)
                if recovered_code_count:
                    lines = merged_lines
                    warnings.append(
                        f'PDF parser restored fuller product codes on {recovered_code_count} line(s).'
                    )
            document_type = (parsed.get('document_type') or '').strip().lower()
            if not document_type and self._looks_like_supplier_credit_note_text(text):
                document_type = 'credit_note'
            if document_type in {'refund', 'credit_note', 'creditnote'}:
                warnings.append('Supplier credit note / refund detected.')

            job._ensure_async_not_cancelled()
            duplicate = job._find_duplicate_job(
                source=job.source,
                partner_id=supplier.id if supplier else False,
                invoice_number=invoice_number,
                invoice_date=self._safe_date(invoice_date_value),
                amount_total=pdf_totals.get('amount_total') or self._safe_float(parsed.get('amount_total')),
                document_type=document_type or 'invoice',
            )
            duplicate_of_id = False
            duplicate_warning = False
            if duplicate and duplicate.id != job.id:
                duplicate_of_id = duplicate.id
                duplicate_warning = f'Duplicate supplier invoice already exists: {duplicate.display_name}'
                warnings.append(duplicate_warning)
            if not used_pdf_parser_lines and len(fallback_lines) > len(ai_lines):
                lines = fallback_lines
                used_pdf_parser_lines = True
                warnings.append(
                    f'AI extracted {len(ai_lines)} lines; PDF parser found {len(fallback_lines)} lines. Using PDF parser lines.'
                )

            job._report_async_progress(80.0, _('Matching products and normalizing lines'))
            total_lines = len(lines)
            job._emit_match_runtime_event(
                phase='stage_start',
                detail='starting extracted line normalization',
                line_total=total_lines,
                supplier=supplier,
                extra={
                    'ai_line_count': len(ai_lines),
                    'fallback_line_count': len(fallback_lines),
                    'normalized_line_target_count': total_lines,
                },
            )
            normalized_lines = []
            for line_index, line in enumerate(lines, start=1):
                job._ensure_async_not_cancelled()
                line_progress = 80.0
                if total_lines:
                    line_progress += min(14.0, (line_index - 1) * 14.0 / total_lines)
                job._report_async_progress(
                    line_progress,
                    _('Matching products and normalizing lines (%(current)s/%(total)s)') % {
                        'current': line_index,
                        'total': total_lines,
                    },
                )
                normalized_line = job.with_context(
                    invoice_ingest_match_line_index=line_index,
                    invoice_ingest_match_line_total=total_lines,
                )._normalize_payload_line(
                    line,
                    supplier=supplier,
                    default_vat_rate=pdf_totals.get('vat_rate') or self._safe_float(parsed.get('vat_rate')),
                )
                if normalized_line:
                    normalized_lines.append(normalized_line)
            job._emit_match_runtime_event(
                phase='stage_complete',
                detail='finished extracted line normalization',
                line_total=total_lines,
                supplier=supplier,
                extra={'normalized_line_count': len(normalized_lines)},
            )

            payload = job._get_payload_dict()
            payload['openai'] = {
                'model': job.ai_model or job._default_ai_model(),
                'raw': parsed,
                'normalized': {
                    'supplier_name': supplier_name,
                    'supplier_code': parsed.get('supplier_code'),
                    'supplier_vat': pdf_header.get('supplier_vat'),
                    'invoice_number': invoice_number,
                    'invoice_date': invoice_date_value,
                    'invoice_due_date': invoice_due_date_value,
                    'invoice_currency': currency_name or currency.name,
                    'vat_rate': pdf_totals.get('vat_rate') or self._safe_float(parsed.get('vat_rate')),
                    'amount_total': pdf_totals.get('amount_total') or self._safe_float(parsed.get('amount_total')),
                    'confidence': self._safe_float(parsed.get('confidence')),
                    'warnings': warnings,
                    'document_type': document_type or 'invoice',
                    'invoice_lines': normalized_lines,
                },
                'pdf_header': pdf_header,
                'pdf_reconciliation': {
                    'total_excl_vat': pdf_totals.get('total_excl_vat'),
                    'vat_amount': pdf_totals.get('vat_amount'),
                    'amount_total': pdf_totals.get('amount_total'),
                    'fallback_line_count': len(fallback_lines),
                    'ai_line_count': len(ai_lines),
                    'used_pdf_parser_lines': used_pdf_parser_lines,
                    'parsed_source': parsed_source,
                    'openai_attempted': openai_attempted,
                    'openai_failed': bool(openai_error),
                },
            }
            if duplicate_of_id:
                payload['openai']['duplicate_of'] = duplicate_of_id
            vals = {
                'state': 'needs_review',
                'partner_id': supplier.id if supplier else False,
                'invoice_number': invoice_number,
                'invoice_date': self._safe_date(invoice_date_value),
                'amount_total': pdf_totals.get('amount_total') or self._safe_float(parsed.get('amount_total')),
                'vat_rate': pdf_totals.get('vat_rate') or self._safe_float(parsed.get('vat_rate')),
                'currency_id': currency.id,
                'document_type': document_type or 'invoice',
                'ai_confidence': self._safe_float(parsed.get('confidence')),
                'error': duplicate_warning or (
                    f'OpenAI extraction failed; used PDF text parser instead: {openai_error}'
                    if openai_error and fallback_lines
                    else False
                ),
            }
            job._ensure_async_not_cancelled()
            job._report_async_progress(95.0, _('Saving extracted invoice lines'))
            job.write(vals)
            job._set_payload_dict(payload)
            job._replace_lines_from_normalized(normalized_lines)
            job._audit_log(
                action='custom',
                description=f'Invoice OCR extraction completed: {job.display_name}',
                new_values={
                    'ai_model': job.ai_model or job._default_ai_model(),
                    'ai_confidence': job.ai_confidence,
                    'partner_id': supplier.id if supplier else False,
                    'document_type': job.document_type,
                    'used_pdf_fallback_lines': used_pdf_parser_lines,
                    'used_pdf_parser_lines': used_pdf_parser_lines,
                    'parsed_source': parsed_source,
                    'warning_count': len(warnings),
                    'warnings': warnings,
                    'duplicate_of_job_id': duplicate_of_id or False,
                    **job._audit_line_summary(),
                },
            )
