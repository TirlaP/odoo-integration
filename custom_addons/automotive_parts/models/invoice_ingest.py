# -*- coding: utf-8 -*-
import base64
import json
import os
import re
from io import BytesIO
from collections import defaultdict

import requests
from PyPDF2 import PdfReader
from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.osv import expression

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


class InvoiceIngestJob(models.Model):
    _name = 'invoice.ingest.job'
    _description = 'Invoice Ingest Job'
    _order = 'id desc'
    _sql_constraints = [
        (
            'invoice_ingest_source_external_unique',
            'unique(source, external_id)',
            'This ANAF/OCR document was already imported.',
        )
    ]

    name = fields.Char(required=True, default=lambda self: f"Invoice Ingest {fields.Datetime.now()}")
    source = fields.Selection(
        [
            ('manual', 'Manual'),
            ('anaf', 'ANAF e-Factura'),
            ('ocr', 'OCR/AI'),
        ],
        default='manual',
        required=True,
        index=True,
    )
    state = fields.Selection(
        [
            ('pending', 'Pending'),
            ('running', 'Running'),
            ('needs_review', 'Needs Review'),
            ('done', 'Done'),
            ('failed', 'Failed'),
        ],
        default='pending',
        index=True,
    )

    picking_id = fields.Many2one('stock.picking', string='Reception (Optional)')
    attachment_id = fields.Many2one('ir.attachment', string='Invoice File (PDF/Image)')

    partner_id = fields.Many2one('res.partner', string='Supplier')
    invoice_number = fields.Char('Invoice Number')
    invoice_date = fields.Date('Invoice Date')
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id)
    amount_total = fields.Monetary('Total Amount', currency_field='currency_id')
    vat_rate = fields.Float('VAT Rate (%)')

    external_id = fields.Char('External ID', help='ANAF message/document identifier (when source=ANAF).')
    payload_json = fields.Text('Payload JSON')

    account_move_id = fields.Many2one('account.move', string='Vendor Bill')
    error = fields.Text()
    line_ids = fields.One2many('invoice.ingest.job.line', 'job_id', string='Extracted Lines')
    ai_model = fields.Char(
        'AI Model',
        default=lambda self: self._default_ai_model(),
        help='Used when extracting invoice details with OpenAI.',
    )
    ai_confidence = fields.Float('AI Confidence (%)')

    def action_open_upload_wizard(self, *args, **kwargs):
        self.ensure_one()
        return {
            'name': 'Import AI (PDF)',
            'type': 'ir.actions.act_window',
            'res_model': 'invoice.ingest.upload.wizard',
            'view_mode': 'form',
            'target': 'new',
        }

    def action_open_react_view(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': f'/automotive/invoice-ingest/react/{self.id}',
            'target': 'self',
        }

    @api.model
    def _default_ai_model(self):
        return (
            os.getenv('OPENAI_INVOICE_MODEL')
            or os.getenv('OPENAI_MODEL')
            or self.env['ir.config_parameter'].sudo().get_param('automotive.openai_model')
            or 'gpt-4o-mini'
        )

    @api.model
    def _get_openai_api_key(self):
        return (
            os.getenv('OPENAI_API_KEY')
            or self.env['ir.config_parameter'].sudo().get_param('automotive.openai_api_key')
        )

    @api.model
    def _normalize_invoice_number(self, invoice_number):
        return (invoice_number or '').strip()

    @api.model
    def _find_duplicate_job(self, source, external_id=None, partner_id=None, invoice_number=None, invoice_date=None):
        source = source or 'manual'
        if external_id:
            existing = self.search(
                [('source', '=', source), ('external_id', '=', external_id)],
                limit=1,
            )
            if existing:
                return existing

        normalized_invoice = self._normalize_invoice_number(invoice_number)
        if partner_id and normalized_invoice:
            domain = [
                ('source', '=', source),
                ('partner_id', '=', partner_id),
                ('invoice_number', '=', normalized_invoice),
            ]
            if invoice_date:
                domain.append(('invoice_date', '=', invoice_date))
            return self.search(domain, order='id desc', limit=1)

        return self.browse()

    @api.model
    def upsert_invoice_job(
        self,
        *,
        source='manual',
        external_id=None,
        partner_id=None,
        invoice_number=None,
        invoice_date=None,
        amount_total=None,
        currency_id=None,
        picking_id=None,
        attachment_id=None,
        payload=None,
    ):
        existing = self._find_duplicate_job(
            source=source,
            external_id=external_id,
            partner_id=partner_id,
            invoice_number=invoice_number,
            invoice_date=invoice_date,
        )
        if existing:
            vals = {}
            if existing.state == 'failed':
                vals['state'] = 'pending'
            if not existing.partner_id and partner_id:
                vals['partner_id'] = partner_id
            if not existing.invoice_number and invoice_number:
                vals['invoice_number'] = self._normalize_invoice_number(invoice_number)
            if not existing.invoice_date and invoice_date:
                vals['invoice_date'] = invoice_date
            if (not existing.amount_total) and amount_total:
                vals['amount_total'] = amount_total
            if not existing.currency_id and currency_id:
                vals['currency_id'] = currency_id
            if not existing.picking_id and picking_id:
                vals['picking_id'] = picking_id
            if not existing.attachment_id and attachment_id:
                vals['attachment_id'] = attachment_id
            if vals:
                existing.write(vals)
            if payload:
                existing._set_payload(payload)
            return existing, False

        vals = {
            'name': f'{(source or "manual").upper()} - {invoice_number or external_id or fields.Datetime.now()}',
            'source': source or 'manual',
            'state': 'pending',
            'external_id': external_id,
            'partner_id': partner_id,
            'invoice_number': self._normalize_invoice_number(invoice_number),
            'invoice_date': invoice_date,
            'amount_total': amount_total or 0.0,
            'currency_id': currency_id or self.env.company.currency_id.id,
            'picking_id': picking_id,
            'attachment_id': attachment_id,
        }
        job = self.create(vals)
        if payload:
            job._set_payload(payload)
        return job, True

    def _set_payload(self, payload):
        self.ensure_one()
        if payload is None or payload is False:
            self.payload_json = False
            return
        if isinstance(payload, str):
            self.payload_json = payload
            return
        self.payload_json = json.dumps(payload, ensure_ascii=False, default=str)

    def action_mark_needs_review(self):
        for job in self:
            job.write({'state': 'needs_review'})

    def _get_payload_dict(self):
        self.ensure_one()
        if not self.payload_json:
            return {}
        try:
            value = json.loads(self.payload_json)
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _set_payload_dict(self, payload):
        self.ensure_one()
        self._set_payload(payload)

    def _extract_pdf_text(self):
        self.ensure_one()
        if not self.attachment_id or not self.attachment_id.datas:
            raise UserError('Attach a PDF first.')
        if self.attachment_id.mimetype and 'pdf' not in (self.attachment_id.mimetype or '').lower():
            raise UserError('The attached file is not a PDF.')

        binary = base64.b64decode(self.attachment_id.datas)
        reader = PdfReader(BytesIO(binary))
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or '')
            except Exception:
                continue
        text = '\n'.join(pages).strip()
        return text

    @api.model
    def _safe_money(self, value, default=0.0):
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

    @api.model
    def _extract_invoice_totals_from_text(self, text):
        if not text:
            return {}

        out = {}
        vat_match = re.search(r'Cota\s*T\.V\.A\.\s*:?\s*([0-9]+(?:[.,][0-9]+)?)\s*%', text, re.IGNORECASE)
        if vat_match:
            out['vat_rate'] = self._safe_money(vat_match.group(1), default=0.0)

        semn_matches = list(
            re.finditer(
                r'Semnaturile\s+([0-9]{1,3}(?:[.,][0-9]{3})*[.,][0-9]{2})\s+([0-9]{1,3}(?:[.,][0-9]{3})*[.,][0-9]{2})',
                text,
                re.IGNORECASE,
            )
        )
        if semn_matches:
            last = semn_matches[-1]
            out['total_excl_vat'] = self._safe_money(last.group(1), default=0.0)
            out['vat_amount'] = self._safe_money(last.group(2), default=0.0)

        plata_matches = list(
            re.finditer(
                r'Total\s+de\s+plata[\s\S]{0,80}?([0-9]{1,3}(?:[.,][0-9]{3})*[.,][0-9]{2})',
                text,
                re.IGNORECASE,
            )
        )
        if plata_matches:
            out['amount_total'] = self._safe_money(plata_matches[-1].group(1), default=0.0)
        elif out.get('total_excl_vat') or out.get('vat_amount'):
            out['amount_total'] = (out.get('total_excl_vat') or 0.0) + (out.get('vat_amount') or 0.0)

        return out

    @api.model
    def _extract_invoice_lines_from_text(self, text, default_vat_rate=0.0):
        if not text:
            return []

        row_re = re.compile(
            r'^\s*(\d{1,3})\s+([A-Z]{2,6})\s+'
            r'([0-9]+(?:[.,][0-9]+)?)\s+'
            r'([0-9]{1,3}(?:[.,][0-9]{3})*[.,][0-9]{2})\s+'
            r'([0-9]{1,3}(?:[.,][0-9]{3})*[.,][0-9]{2})\s+'
            r'([0-9]{1,3}(?:[.,][0-9]{3})*[.,][0-9]{2})\s+'
            r'(.+?)\s*$'
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
                    'quantity': self._safe_money(row_match.group(3), default=1.0) or 1.0,
                    'unit_price': self._safe_money(row_match.group(4), default=0.0),
                    'line_total_excl_vat': self._safe_money(row_match.group(5), default=0.0),
                    'line_vat_amount': self._safe_money(row_match.group(6), default=0.0),
                    'desc_parts': [row_match.group(7).strip()],
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
            out.append({
                'quantity': row.get('quantity') or 1.0,
                'product_code_raw': parsed.get('product_code_raw') or description,
                'product_code': parsed.get('product_code_primary') or False,
                'supplier_brand': parsed.get('supplier_brand') or '',
                'product_description': description,
                'unit_price': row.get('unit_price') or 0.0,
                'vat_rate': vat_rate,
            })
        return out

    @api.model
    def _safe_float(self, value, default=0.0):
        if value in (None, False, ''):
            return default
        try:
            return float(str(value).replace(',', '.'))
        except Exception:
            return default

    @api.model
    def _safe_date(self, value):
        if not value:
            return False
        try:
            return fields.Date.to_date(value)
        except Exception:
            return False

    def _find_supplier_partner(self, supplier_name=None, supplier_code=None):
        self.ensure_one()
        Partner = self.env['res.partner']
        if self.partner_id:
            return self.partner_id

        # Try exact code first (legacy app seems to use short codes like AD/MT/CX).
        if supplier_code:
            clean_code = supplier_code.strip()
            partner = Partner.search([('name', '=ilike', clean_code)], limit=1)
            if partner:
                return partner
            partner = Partner.search([('ref', '=ilike', clean_code)], limit=1)
            if partner:
                return partner

        if supplier_name:
            partner = Partner.search([('name', '=ilike', supplier_name.strip())], limit=1)
            if partner:
                return partner
            partner = Partner.search([('name', 'ilike', supplier_name.strip())], limit=1)
            if partner:
                return partner
        return Partner

    @api.model
    def _normalize_cui_digits(self, value):
        if not value:
            return ''
        return ''.join(ch for ch in str(value) if ch.isdigit())

    def _resolve_supplier_for_billing(self):
        """Best-effort supplier resolution for bill creation."""
        self.ensure_one()
        if self.partner_id:
            return self.partner_id

        # 1) If job is linked to a reception, use its supplier.
        if self.picking_id and self.picking_id.partner_id:
            self.partner_id = self.picking_id.partner_id.id
            return self.partner_id

        payload = self._get_payload_dict()

        # 2) OpenAI normalized supplier hints.
        normalized = (payload.get('openai') or {}).get('normalized') or {}
        supplier = self._find_supplier_partner(
            supplier_name=(normalized.get('supplier_name') or '').strip(),
            supplier_code=(normalized.get('supplier_code') or '').strip(),
        )
        if supplier:
            self.partner_id = supplier.id
            return self.partner_id

        # 3) ANAF parsed CUI fallback.
        parsed_payload = payload.get('parsed') or {}
        supplier_cui = self._normalize_cui_digits(parsed_payload.get('supplier_cui'))
        if supplier_cui:
            Partner = self.env['res.partner']
            supplier = (
                Partner.search([('vat', '=', supplier_cui)], limit=1)
                or Partner.search([('vat', '=ilike', f'RO{supplier_cui}')], limit=1)
                or Partner.search([('cui', '=', supplier_cui)], limit=1)
                or Partner.search([('cui', '=ilike', f'RO{supplier_cui}')], limit=1)
            )
            if supplier:
                self.partner_id = supplier.id
                return self.partner_id

        # 4) Infer from matched products if there is exactly one clear supplier.
        product_suppliers = self.line_ids.mapped('product_id.main_supplier_id').filtered(lambda p: p)
        if len(product_suppliers) == 1:
            self.partner_id = product_suppliers.id
            return self.partner_id

        seller_suppliers = self.line_ids.mapped('product_id.product_tmpl_id.seller_ids.partner_id').filtered(lambda p: p)
        if len(seller_suppliers) == 1:
            self.partner_id = seller_suppliers.id
            return self.partner_id

        return self.env['res.partner']

    @api.model
    def _normalize_code_value(self, value):
        raw = (value or '').strip().upper()
        if not raw:
            return ''
        # Normalize mixed dash characters from OCR/PDF extraction.
        raw = (
            raw.replace('–', '-')
            .replace('—', '-')
            .replace('−', '-')
        )
        return ' '.join(raw.split())

    @api.model
    def _compact_code(self, value):
        return re.sub(r'[^A-Z0-9]', '', self._normalize_code_value(value))

    @api.model
    def _is_supplier_token(self, token):
        token = self._normalize_code_value(token)
        compact = self._compact_code(token)
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

    @api.model
    def _extract_supplier_brand(self, raw_text, supplier_hint=None):
        if supplier_hint and self._is_supplier_token(supplier_hint):
            return self._compact_code(supplier_hint)

        text = self._normalize_code_value(raw_text)
        if not text:
            return ''

        parts = [part.strip() for part in re.split(r'\s+-\s+', text) if part.strip()]
        for part in reversed(parts):
            if self._is_supplier_token(part):
                return self._compact_code(part)

        tokens = text.split()
        for token in reversed(tokens):
            if self._is_supplier_token(token):
                return self._compact_code(token)
        return ''

    @api.model
    def _extract_primary_code(self, raw_text):
        text = self._normalize_code_value(raw_text)
        if not text:
            return ''

        parts = [part.strip() for part in re.split(r'\s+-\s+', text) if part.strip()]
        if len(parts) >= 2:
            return self._extract_primary_code(parts[0])

        tokens = [tok.strip(",.;:()[]") for tok in text.split() if tok.strip(",.;:()[]")]
        if not tokens:
            return ''

        first = re.sub(r'[^A-Z0-9-]', '', self._normalize_code_value(tokens[0]))
        if not first:
            return ''

        selected = [first]
        if self._compact_code(first) in INVOICE_CODE_STOP_WORDS:
            return ''

        # Handles patterns like "VKBA 6649" / "TI 15 92"
        for token in tokens[1:4]:
            normalized = self._normalize_code_value(token)
            compact = re.sub(r'[^A-Z0-9-]', '', normalized)
            if not compact:
                break
            if self._compact_code(compact) in INVOICE_CODE_STOP_WORDS:
                break

            first_compact = self._compact_code(selected[0])
            first_is_short_alpha = first_compact.isalpha() and len(first_compact) <= 5
            if first_is_short_alpha and compact.isdigit() and len(compact) <= 4:
                selected.append(compact)
                continue
            if len(selected) >= 2 and selected[1].isdigit() and compact.isdigit() and len(compact) <= 4:
                selected.append(compact)
                continue
            break

        return ' '.join(selected)

    @api.model
    def _trimmed_code_variants(self, code):
        compact = self._compact_code(code)
        variants = []

        def _add(candidate):
            if candidate and candidate not in variants:
                variants.append(candidate)

        for suffix in INVOICE_TRIM_SUFFIXES:
            if compact.endswith(suffix) and len(compact) > len(suffix) + 3:
                _add(compact[: -len(suffix)])
        return variants

    @api.model
    def _progressive_tail_trim_candidates(self, code):
        """
        Last-resort fallback for codes where supplier/suffix is glued to the end,
        e.g. A1353DREIS -> A1353, AVX10X700CT -> AVX10X700.
        We trim one trailing letter at a time with hard limits.
        """
        compact = self._compact_code(code)
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

    @api.model
    def _code_candidates(self, value, extra=None):
        candidates = []

        def _add(raw_value):
            normalized = self._normalize_code_value(raw_value)
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

    @api.model
    def _parse_invoice_line_identity(self, product_code_raw, product_description='', supplier_hint=''):
        raw_code = self._normalize_code_value(product_code_raw)
        raw_description = self._normalize_code_value(product_description)

        # Drop NC/CPV metadata, these are fiscal/procurement classifications, not SKU identifiers.
        combined = f'{raw_code} {raw_description}'.strip()
        for marker in INVOICE_META_PREFIXES:
            combined = re.sub(rf'\b{marker}\s*[^\s]+', ' ', combined, flags=re.IGNORECASE)
        combined = ' '.join(combined.split())

        primary = self._extract_primary_code(raw_code or combined)
        if not primary:
            primary = self._extract_primary_code(combined)

        supplier_brand = self._extract_supplier_brand(raw_code or combined, supplier_hint=supplier_hint)

        parsed = {
            'product_code_raw': raw_code or product_code_raw or '',
            'product_code_primary': primary or '',
            'product_code_compact': self._compact_code(primary),
            'supplier_brand': supplier_brand,
            'code_candidates': [],
        }
        parsed['code_candidates'] = self._code_candidates(
            parsed['product_code_primary'],
            extra=self._trimmed_code_variants(parsed['product_code_primary']),
        )
        return parsed

    @api.model
    def _supplier_product_domain(self, supplier):
        if not supplier:
            return []
        supplier_rec = supplier
        if not isinstance(supplier_rec, models.BaseModel):
            try:
                supplier_rec = self.env['res.partner'].browse(int(supplier))
            except Exception:
                return []
        supplier_rec = supplier_rec[:1]
        if not supplier_rec or supplier_rec._name != 'res.partner':
            return []
        return ['|', ('main_supplier_id', '=', supplier_rec.id), ('product_tmpl_id.seller_ids.partner_id', '=', supplier_rec.id)]

    @api.model
    def _supplier_brand_domain(self, supplier_brand):
        brand = self._normalize_code_value(supplier_brand)
        if not brand:
            return []
        return [
            '|', '|', '|',
            ('product_tmpl_id.tecdoc_supplier_name', '=ilike', brand),
            ('product_tmpl_id.tecdoc_variant_ids.supplier_name', '=ilike', brand),
            ('main_supplier_id.name', '=ilike', brand),
            ('main_supplier_id.ref', '=ilike', brand),
        ]

    @api.model
    def _match_by_catalog_lookup(self, code, supplier_domain=None, supplier_brand_domain=None):
        Product = self.env['product.product']
        key = self._compact_code(code)
        if not key:
            return Product, ''

        lookup_domain = [
            '|', '|', '|',
            ('product_tmpl_id.tecdoc_article_no_key', '=', key),
            ('product_tmpl_id.tecdoc_variant_ids.oem_number_ids.number_key', '=', key),
            ('product_tmpl_id.tecdoc_variant_ids.ean_ids.ean_key', '=', key),
            ('product_tmpl_id.tecdoc_variant_ids.cross_link_ids.cross_number_id.number_key', '=', key),
        ]
        scoped_domains = []
        if supplier_domain:
            scoped_domains.append((supplier_domain, ' supplier'))
        if supplier_brand_domain:
            scoped_domains.append((supplier_brand_domain, ' supplier brand'))
        scoped_domains.append(([], ''))
        for extra_domain, reason_suffix in scoped_domains:
            if extra_domain:
                product = Product.search(expression.AND([lookup_domain, extra_domain]), limit=1)
            else:
                product = Product.search(lookup_domain, limit=1)
            if product:
                return product, f'lookup{reason_suffix}'
        return Product, ''

    def _match_product(self, product_code, supplier=None, product_description=None, supplier_brand=None, extra_codes=None):
        product, _meta = self._match_product_with_meta(
            product_code=product_code,
            supplier=supplier,
            product_description=product_description,
            supplier_brand=supplier_brand,
            extra_codes=extra_codes,
        )
        return product

    @api.model
    def _brand_from_matched_product(self, product):
        product = (product or self.env['product.product'])[:1]
        if not product:
            return '', False

        brand_name = (product.tecdoc_supplier_name or '').strip()
        supplier_id = False
        try:
            supplier_id = int(product.tecdoc_supplier_id or 0) or False
        except Exception:
            supplier_id = False

        variants = product.product_tmpl_id.tecdoc_variant_ids
        variant = variants[:1]
        article_no = (product.tecdoc_article_no or '').strip().upper()
        if variants and article_no:
            by_article = variants.filtered(lambda v: (v.article_no or '').strip().upper() == article_no)
            if by_article:
                variant = by_article[:1]

        if variant:
            if not brand_name:
                brand_name = (variant.supplier_name or '').strip()
            if not supplier_id:
                supplier_id = (
                    variant.supplier_external_id
                    or (variant.supplier_id.supplier_id if variant.supplier_id else False)
                    or False
                )

        if not brand_name and product.main_supplier_id:
            brand_name = (product.main_supplier_id.name or '').strip()

        return brand_name, supplier_id or False

    def _match_product_with_meta(self, product_code, supplier=None, product_description=None, supplier_brand=None, extra_codes=None):
        self.ensure_one()
        Product = self.env['product.product']
        supplier_domain = self._supplier_product_domain(supplier)
        supplier_brand_domain = self._supplier_brand_domain(supplier_brand)
        codes = self._code_candidates(product_code, extra=extra_codes)

        # 1) Strict exact matching by code fields; try constrained scopes first.
        scoped_domains = []
        if supplier_domain:
            scoped_domains.append((supplier_domain, ' supplier'))
        if supplier_brand_domain:
            scoped_domains.append((supplier_brand_domain, ' supplier brand'))
        scoped_domains.append(([], ''))

        # Prefer article-based matching first (TecDoc articleNo / internal references),
        # then fall back to supplier/barcode fields.
        for field_name in ('tecdoc_article_no', 'default_code', 'supplier_code', 'barcode_internal', 'barcode'):
            for code in codes:
                base_domain = [(field_name, '=', code)]
                for extra_domain, reason_suffix in scoped_domains:
                    if extra_domain:
                        product = Product.search(expression.AND([base_domain, extra_domain]), limit=1)
                    else:
                        product = Product.search(base_domain, limit=1)
                    if product:
                        return product, {
                            'method': f'exact:{field_name}{reason_suffix}',
                            'matched_code': code,
                            'confidence': 100.0 if reason_suffix else 96.0,
                        }

        # 2) Exact TecDoc lookup through variant/oem/ean/cross relations.
        for code in codes:
            product, lookup_reason = self._match_by_catalog_lookup(
                code=code,
                supplier_domain=supplier_domain,
                supplier_brand_domain=supplier_brand_domain,
            )
            if product:
                confidence = 95.0 if 'supplier' in lookup_reason else 84.0
                return product, {
                    'method': lookup_reason or 'lookup',
                    'matched_code': code,
                    'confidence': confidence,
                }

        # 2b) Progressive tail-trim pass (only if strict candidates failed).
        trim_candidates = self._progressive_tail_trim_candidates(product_code)
        for code in trim_candidates:
            # Exact match only for this fallback.
            for field_name in ('tecdoc_article_no', 'default_code', 'supplier_code', 'barcode_internal', 'barcode'):
                base_domain = [(field_name, '=', code)]
                for extra_domain, reason_suffix in scoped_domains:
                    if extra_domain:
                        product = Product.search(expression.AND([base_domain, extra_domain]), limit=1)
                    else:
                        product = Product.search(base_domain, limit=1)
                    if product:
                        confidence = 83.0 if reason_suffix else 75.0
                        return product, {
                            'method': f'progressive_trim:{field_name}{reason_suffix}',
                            'matched_code': code,
                            'confidence': confidence,
                        }

            product, lookup_reason = self._match_by_catalog_lookup(
                code=code,
                supplier_domain=supplier_domain,
                supplier_brand_domain=supplier_brand_domain,
            )
            if product:
                confidence = 82.0 if 'supplier' in lookup_reason else 74.0
                return product, {
                    'method': f'progressive_trim:{lookup_reason or "lookup"}',
                    'matched_code': code,
                    'confidence': confidence,
                }

        # 3) Relaxed `ilike` only for code-like fields and only when we do have a code.
        for field_name in ('tecdoc_article_no', 'default_code', 'supplier_code'):
            for code in codes:
                if len(self._compact_code(code)) < 4:
                    continue
                base_domain = [(field_name, '=ilike', code)]
                for extra_domain, reason_suffix in scoped_domains:
                    if extra_domain:
                        product = Product.search(expression.AND([base_domain, extra_domain]), limit=1)
                    else:
                        product = Product.search(base_domain, limit=1)
                    if product:
                        confidence = 86.0 if reason_suffix else 80.0
                        return product, {
                            'method': f'ilike:{field_name}{reason_suffix}',
                            'matched_code': code,
                            'confidence': confidence,
                        }

        # 4) Description fallback only when no code was parsed at all.
        description = (product_description or '').strip()
        if description and not codes:
            exact_name_domain = [('name', '=ilike', ' '.join(description.split()))]
            for extra_domain, reason_suffix in scoped_domains:
                if extra_domain:
                    product = Product.search(expression.AND([exact_name_domain, extra_domain]), limit=1)
                else:
                    product = Product.search(exact_name_domain, limit=1)
                if product:
                    confidence = 70.0 if reason_suffix else 62.0
                    return product, {
                        'method': f'description_exact{reason_suffix}',
                        'matched_code': '',
                        'confidence': confidence,
                    }

        return Product, {
            'method': 'not_found',
            'matched_code': '',
            'confidence': 0.0,
        }

    def _replace_lines_from_normalized(self, normalized_lines):
        self.ensure_one()
        commands = [(5, 0, 0)]
        sequence = 1
        for line in normalized_lines or []:
            if not isinstance(line, dict):
                continue
            raw_code = (line.get('product_code_raw') or line.get('product_code') or '').strip()
            parsed = self._parse_invoice_line_identity(
                raw_code,
                product_description=(line.get('product_description') or '').strip(),
                supplier_hint=(line.get('supplier_brand') or '').strip(),
            )
            parsed_code = parsed.get('product_code_primary') or (line.get('product_code') or '').strip()
            supplier_brand = parsed.get('supplier_brand') or (line.get('supplier_brand') or '').strip()
            try:
                supplier_brand_id = int(line.get('supplier_brand_id') or 0) or False
            except Exception:
                supplier_brand_id = False
            product_id = line.get('matched_product_id')
            match_method = (line.get('match_method') or '').strip()
            match_confidence = self._safe_float(line.get('match_confidence'))
            matched_product = self.env['product.product']
            if product_id:
                try:
                    matched_product = self.env['product.product'].browse(int(product_id)).exists()
                except Exception:
                    matched_product = self.env['product.product']
                    product_id = False
            if not product_id:
                product, match_meta = self._match_product_with_meta(
                    parsed_code,
                    supplier=self.partner_id,
                    product_description=(line.get('product_description') or '').strip(),
                    supplier_brand=supplier_brand,
                    extra_codes=parsed.get('code_candidates') or [],
                )
                if not product and parsed_code:
                    # Keep UI code cleaner if no strict match and we can isolate a canonical base.
                    progressive_candidates = self._progressive_tail_trim_candidates(parsed_code)
                    if progressive_candidates:
                        parsed_code = progressive_candidates[-1]
                if product and match_meta.get('confidence', 0.0) >= AUTO_MATCH_CONFIDENCE_THRESHOLD:
                    product_id = product.id
                    matched_product = product
                else:
                    product_id = False
                match_method = match_meta.get('method', '')
                match_confidence = match_meta.get('confidence', 0.0)
            if matched_product:
                canonical_brand, canonical_supplier_id = self._brand_from_matched_product(matched_product)
                if canonical_brand:
                    supplier_brand = canonical_brand
                supplier_brand_id = canonical_supplier_id or supplier_brand_id or False
            else:
                supplier_brand_id = False
            commands.append((0, 0, {
                'sequence': sequence,
                'quantity': self._safe_float(line.get('quantity'), default=1.0) or 1.0,
                'product_code_raw': raw_code,
                'product_code': parsed_code,
                'supplier_brand': supplier_brand,
                'supplier_brand_id': supplier_brand_id,
                'product_description': (line.get('product_description') or '').strip(),
                'unit_price': self._safe_float(line.get('unit_price'), default=0.0),
                'vat_rate': self._safe_float(line.get('vat_rate'), default=self.vat_rate or 0.0),
                'product_id': product_id or False,
                'match_method': match_method,
                'match_confidence': match_confidence,
            }))
            sequence += 1
        self.write({'line_ids': commands})

    def _get_default_incoming_picking_type(self):
        self.ensure_one()
        company = self.env.company
        PickingType = self.env['stock.picking.type']
        picking_type = PickingType.search(
            [('code', '=', 'incoming'), ('warehouse_id.company_id', '=', company.id)],
            order='sequence, id',
            limit=1,
        )
        if not picking_type:
            picking_type = PickingType.search(
                [('code', '=', 'incoming'), ('company_id', '=', company.id)],
                order='sequence, id',
                limit=1,
            )
        if not picking_type:
            raise UserError('No incoming picking type found. Configure Inventory receipts first.')
        return picking_type

    def _collect_receipt_quantities(self):
        self.ensure_one()
        quantities = defaultdict(float)
        unmatched_count = 0
        for line in self.line_ids.sorted('sequence'):
            qty = self._safe_float(line.quantity, default=0.0)
            if qty <= 0:
                continue
            if not line.product_id:
                unmatched_count += 1
                continue
            quantities[line.product_id.id] += qty
        return dict(quantities), unmatched_count

    def _ensure_receipt(self, supplier):
        self.ensure_one()
        if self.picking_id and self.picking_id.exists() and self.picking_id.state != 'cancel':
            picking = self.picking_id
            vals = {}
            if supplier and not picking.partner_id:
                vals['partner_id'] = supplier.id
            if self.invoice_number and not picking.origin:
                vals['origin'] = f'Invoice {self.invoice_number}'
            if vals:
                picking.write(vals)
            return picking, False

        picking_type = self._get_default_incoming_picking_type()
        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'partner_id': supplier.id if supplier else False,
            'origin': f'Invoice {self.invoice_number}' if self.invoice_number else self.name,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
        })
        self.picking_id = picking.id
        return picking, True

    def _sync_receipt_moves(self, picking, product_quantities):
        self.ensure_one()
        if picking.state in {'done', 'cancel'}:
            return 0

        Move = self.env['stock.move']
        MoveLine = self.env['stock.move.line']
        updated = 0
        for product_id, qty in product_quantities.items():
            if qty <= 0:
                continue
            product = self.env['product.product'].browse(product_id).exists()
            if not product:
                continue

            move = picking.move_ids_without_package.filtered(
                lambda m: m.product_id.id == product.id and m.state not in {'done', 'cancel'}
            )[:1]

            if move:
                move.write({
                    'product_uom_qty': qty,
                    'quantity': qty,
                    'product_uom': product.uom_id.id,
                })
            else:
                move = Move.create({
                    'name': product.display_name,
                    'product_id': product.id,
                    'product_uom_qty': qty,
                    'quantity': qty,
                    'product_uom': product.uom_id.id,
                    'picking_id': picking.id,
                    'location_id': picking.location_id.id,
                    'location_dest_id': picking.location_dest_id.id,
                })
            if move.state == 'draft':
                move._action_confirm()

            move_line = move.move_line_ids.filtered(
                lambda l: l.product_id.id == product.id
                and l.location_id.id == picking.location_id.id
                and l.location_dest_id.id == picking.location_dest_id.id
                and not l.lot_id
            )[:1]
            if move_line:
                move_line.write({
                    'product_uom_id': product.uom_id.id,
                    'quantity': qty,
                })
                extra_lines = (move.move_line_ids - move_line).filtered(
                    lambda l: l.product_id.id == product.id and not l.lot_id and l.state != 'done'
                )
                if extra_lines:
                    extra_lines.unlink()
            else:
                MoveLine.create({
                    'picking_id': picking.id,
                    'move_id': move.id,
                    'product_id': product.id,
                    'product_uom_id': product.uom_id.id,
                    'location_id': picking.location_id.id,
                    'location_dest_id': picking.location_dest_id.id,
                    'quantity': qty,
                })
            updated += 1

        return updated

    def _validate_receipt(self, picking):
        self.ensure_one()
        if not picking or picking.state in {'done', 'cancel'}:
            return bool(picking and picking.state == 'done')

        if picking.state == 'draft':
            picking.action_confirm()
        result = picking.button_validate()
        if isinstance(result, dict) and result.get('res_model') == 'stock.backorder.confirmation' and result.get('res_id'):
            self.env['stock.backorder.confirmation'].browse(result['res_id']).process_cancel_backorder()
        return picking.state == 'done'

    def _auto_create_or_update_receipt(self, supplier):
        self.ensure_one()
        product_quantities, unmatched_count = self._collect_receipt_quantities()
        if not product_quantities:
            return {
                'created': False,
                'updated_lines': 0,
                'validated': False,
                'unmatched_count': unmatched_count,
                'reason': 'no_matched_products',
            }

        picking, created = self._ensure_receipt(supplier=supplier)
        updated_lines = self._sync_receipt_moves(picking, product_quantities)
        validated = self._validate_receipt(picking)
        return {
            'created': created,
            'updated_lines': updated_lines,
            'validated': validated,
            'unmatched_count': unmatched_count,
            'reason': '',
        }

    def action_extract_with_openai(self):
        api_key = self._get_openai_api_key()
        if not api_key:
            raise UserError(
                'Missing OPENAI_API_KEY. Set env var OPENAI_API_KEY or config parameter automotive.openai_api_key.'
            )

        for job in self:
            text = job._extract_pdf_text()
            if not text or len(text) < 20:
                raise UserError(
                    'PDF text extraction returned no usable text. Use ANAF XML import or connect an OCR provider.'
                )
            pdf_totals = job._extract_invoice_totals_from_text(text)

            prompt = (
                "Extract invoice data from Romanian automotive supplier invoice text. "
                "Return strict JSON with keys: "
                "supplier_name, supplier_code, invoice_number, invoice_date, invoice_due_date, "
                "invoice_currency, vat_rate, amount_total, confidence, warnings, invoice_lines. "
                "invoice_lines is an array of objects with: "
                "quantity, product_code, product_code_raw, supplier_brand, product_description, unit_price. "
                "product_code must be the main article code only (e.g. GDB1956, 56789, VKBA 6649). "
                "supplier_brand should contain only the supplier brand token (e.g. TRW, BOSCH, SKF). "
                "Exclude NC= and CPV= values from product_code. "
                "Use ISO date format YYYY-MM-DD. If unknown, use null. confidence must be 0..100."
            )
            body = {
                'model': job.ai_model or job._default_ai_model(),
                'response_format': {'type': 'json_object'},
                'messages': [
                    {'role': 'system', 'content': 'You are a strict invoice extraction engine. Output valid JSON only.'},
                    {'role': 'user', 'content': f'{prompt}\n\nINVOICE_TEXT:\n{text[:120000]}'},
                ],
            }
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

            supplier = job._find_supplier_partner(
                supplier_name=parsed.get('supplier_name'),
                supplier_code=parsed.get('supplier_code'),
            )

            ai_lines = parsed.get('invoice_lines') or []
            fallback_lines = job._extract_invoice_lines_from_text(
                text,
                default_vat_rate=pdf_totals.get('vat_rate') or self._safe_float(parsed.get('vat_rate')),
            )
            lines = ai_lines
            warnings = parsed.get('warnings') if isinstance(parsed.get('warnings'), list) else []
            if len(fallback_lines) > len(ai_lines):
                lines = fallback_lines
                warnings.append(
                    f'AI extracted {len(ai_lines)} lines; PDF parser found {len(fallback_lines)} lines. Using PDF parser lines.'
                )

            normalized_lines = []
            for line in lines:
                if not isinstance(line, dict):
                    continue
                raw_code = (line.get('product_code_raw') or line.get('product_code') or '').strip()
                description = (line.get('product_description') or '').strip()
                parsed_identity = job._parse_invoice_line_identity(
                    raw_code,
                    product_description=description,
                    supplier_hint=(line.get('supplier_brand') or '').strip(),
                )
                parsed_code = parsed_identity.get('product_code_primary') or (line.get('product_code') or '').strip()
                parsed_supplier_brand = parsed_identity.get('supplier_brand') or (line.get('supplier_brand') or '').strip()
                product, match_meta = job._match_product_with_meta(
                    parsed_code,
                    supplier=supplier,
                    product_description=description,
                    supplier_brand=parsed_supplier_brand,
                    extra_codes=parsed_identity.get('code_candidates') or [],
                )
                if not product and parsed_code:
                    progressive_candidates = job._progressive_tail_trim_candidates(parsed_code)
                    if progressive_candidates:
                        parsed_code = progressive_candidates[-1]
                matched_product_id = (
                    product.id
                    if product and match_meta.get('confidence', 0.0) >= AUTO_MATCH_CONFIDENCE_THRESHOLD
                    else False
                )
                supplier_brand_id = False
                if matched_product_id:
                    canonical_brand, canonical_supplier_id = job._brand_from_matched_product(product)
                    if canonical_brand:
                        parsed_supplier_brand = canonical_brand
                    supplier_brand_id = canonical_supplier_id or False
                normalized_lines.append({
                    'quantity': self._safe_float(line.get('quantity'), default=1.0) or 1.0,
                    'product_code_raw': raw_code,
                    'product_code': parsed_code or False,
                    'supplier_brand': parsed_supplier_brand,
                    'supplier_brand_id': supplier_brand_id,
                    'product_description': description,
                    'unit_price': self._safe_float(line.get('unit_price'), default=0.0),
                    'matched_product_id': matched_product_id,
                    'matched_product_name': product.display_name if product else False,
                    'match_status': (
                        'matched' if matched_product_id else 'not_found'
                    ),
                    'match_method': match_meta.get('method'),
                    'match_confidence': match_meta.get('confidence'),
                })

            currency = self.env.company.currency_id
            currency_name = (parsed.get('invoice_currency') or '').strip().upper()
            if currency_name:
                currency = self.env['res.currency'].search([('name', '=', currency_name)], limit=1) or currency

            payload = job._get_payload_dict()
            payload['openai'] = {
                'model': job.ai_model or job._default_ai_model(),
                'raw': parsed,
                'normalized': {
                    'supplier_name': parsed.get('supplier_name'),
                    'supplier_code': parsed.get('supplier_code'),
                    'invoice_number': parsed.get('invoice_number'),
                    'invoice_date': parsed.get('invoice_date'),
                    'invoice_due_date': parsed.get('invoice_due_date'),
                    'invoice_currency': currency_name or currency.name,
                    'vat_rate': pdf_totals.get('vat_rate') or self._safe_float(parsed.get('vat_rate')),
                    'amount_total': pdf_totals.get('amount_total') or self._safe_float(parsed.get('amount_total')),
                    'confidence': self._safe_float(parsed.get('confidence')),
                    'warnings': warnings,
                    'invoice_lines': normalized_lines,
                },
                'pdf_reconciliation': {
                    'total_excl_vat': pdf_totals.get('total_excl_vat'),
                    'vat_amount': pdf_totals.get('vat_amount'),
                    'amount_total': pdf_totals.get('amount_total'),
                    'fallback_line_count': len(fallback_lines),
                    'ai_line_count': len(ai_lines),
                },
            }
            vals = {
                'state': 'needs_review',
                'partner_id': supplier.id if supplier else False,
                'invoice_number': self._normalize_invoice_number(parsed.get('invoice_number')),
                'invoice_date': self._safe_date(parsed.get('invoice_date')),
                'amount_total': pdf_totals.get('amount_total') or self._safe_float(parsed.get('amount_total')),
                'vat_rate': pdf_totals.get('vat_rate') or self._safe_float(parsed.get('vat_rate')),
                'currency_id': currency.id,
                'ai_confidence': self._safe_float(parsed.get('confidence')),
                'error': False,
            }
            job.write(vals)
            job._set_payload_dict(payload)
            job._replace_lines_from_normalized(normalized_lines)

    def action_run(self):
        for job in self:
            if job.state not in {'pending', 'failed', 'needs_review'}:
                continue
            job.write({'state': 'running', 'error': False})

            # For now this is scaffolding: OCR/ANAF extraction should set header/lines,
            # then create a draft bill and route to "needs_review".
            if job.account_move_id:
                job.write({'state': 'done'})
            else:
                job.write({'state': 'needs_review'})

    def action_create_draft_vendor_bill(self):
        notifications = []
        for job in self:
            supplier = job._resolve_supplier_for_billing()
            if not supplier:
                payload = job._get_payload_dict()
                normalized = (payload.get('openai') or {}).get('normalized') or {}
                hinted_name = (normalized.get('supplier_name') or '').strip()
                hint = f' Extracted invoice supplier hint: {hinted_name}.' if hinted_name else ''
                raise UserError(
                    'Select the invoice supplier first (the vendor who issued the invoice, '
                    'not the per-line product brand).'
                    f'{hint}'
                )
            if not job.invoice_number:
                raise UserError('Set invoice number first.')

            if job.account_move_id:
                move = job.account_move_id
            else:
                existing_move = self.env['account.move'].search(
                    [
                        ('move_type', '=', 'in_invoice'),
                        ('partner_id', '=', supplier.id),
                        ('ref', '=', job.invoice_number),
                        ('state', '!=', 'cancel'),
                    ],
                    order='id desc',
                    limit=1,
                )
                if existing_move:
                    move = existing_move
                else:
                    line_vals = []
                    if job.line_ids:
                        for line in job.line_ids.sorted('sequence'):
                            description = (
                                (line.product_description or '').strip()
                                or (line.product_code or '').strip()
                                or 'Imported invoice line'
                            )
                            vals = {
                                'name': description,
                                'quantity': line.quantity or 1.0,
                                'price_unit': line.discounted_unit_price or line.unit_price or 0.0,
                            }
                            if line.product_id:
                                vals['product_id'] = line.product_id.id
                            line_vals.append((0, 0, vals))
                    else:
                        payload = job._get_payload_dict()
                        parsed_lines = (
                            payload.get('openai', {})
                            .get('normalized', {})
                            .get('invoice_lines', [])
                        )
                        for line in parsed_lines:
                            if not isinstance(line, dict):
                                continue
                            quantity = self._safe_float(line.get('quantity'), default=1.0) or 1.0
                            unit_price = self._safe_float(line.get('unit_price'), default=0.0)
                            description = (
                                (line.get('product_description') or '').strip()
                                or (line.get('product_code') or '').strip()
                                or 'Imported invoice line'
                            )
                            vals = {
                                'name': description,
                                'quantity': quantity,
                                'price_unit': unit_price,
                            }
                            if line.get('matched_product_id'):
                                vals['product_id'] = line['matched_product_id']
                            line_vals.append((0, 0, vals))

                    if not line_vals:
                        line_vals = [
                            (0, 0, {
                                'name': 'Imported invoice (needs review)',
                                'quantity': 1,
                                'price_unit': job.amount_total or 0.0,
                            }),
                        ]

                    move = self.env['account.move'].create({
                        'move_type': 'in_invoice',
                        'partner_id': supplier.id,
                        'ref': job.invoice_number,
                        'invoice_date': job.invoice_date,
                        'invoice_line_ids': line_vals,
                    })

            job.write({'account_move_id': move.id, 'state': 'needs_review'})

            receipt_info = job._auto_create_or_update_receipt(supplier=supplier)

            if job.picking_id:
                job.picking_id.with_context(skip_audit_log=True).write({
                    'supplier_invoice_id': move.id,
                    'supplier_invoice_number': job.invoice_number,
                    'supplier_invoice_date': job.invoice_date,
                })
            if receipt_info.get('reason') == 'no_matched_products':
                notifications.append(
                    f"{job.invoice_number or job.id}: bill created, but receipt skipped (no matched products)."
                )
            else:
                notifications.append(
                    f"{job.invoice_number or job.id}: bill ready; receipt {'created' if receipt_info.get('created') else 'updated'} "
                    f"({receipt_info.get('updated_lines', 0)} lines), validated={bool(receipt_info.get('validated'))}."
                )

        if len(self) == 1 and notifications:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Invoice Import',
                    'message': notifications[0],
                    'type': 'success',
                    'sticky': False,
                },
            }
        return True

    def action_sync_receipt_stock(self):
        notifications = []
        for job in self:
            supplier = job._resolve_supplier_for_billing() or job.partner_id
            receipt_info = job._auto_create_or_update_receipt(supplier=supplier)
            if job.account_move_id and job.picking_id:
                job.picking_id.with_context(skip_audit_log=True).write({
                    'supplier_invoice_id': job.account_move_id.id,
                    'supplier_invoice_number': job.invoice_number,
                    'supplier_invoice_date': job.invoice_date,
                })
            if receipt_info.get('reason') == 'no_matched_products':
                notifications.append(f"{job.invoice_number or job.id}: no matched lines, nothing received.")
            else:
                notifications.append(
                    f"{job.invoice_number or job.id}: receipt {'created' if receipt_info.get('created') else 'updated'} "
                    f"({receipt_info.get('updated_lines', 0)} lines), validated={bool(receipt_info.get('validated'))}."
                )

        if len(self) == 1 and notifications:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Receipt Sync',
                    'message': notifications[0],
                    'type': 'success',
                    'sticky': False,
                },
            }
        return True

    @api.model
    def cron_process_jobs(self):
        job = self.search([('state', '=', 'pending')], order='id asc', limit=1)
        if job:
            job.action_run()


class InvoiceIngestJobLine(models.Model):
    _name = 'invoice.ingest.job.line'
    _description = 'Invoice Ingest Job Line'
    _order = 'sequence, id'

    job_id = fields.Many2one('invoice.ingest.job', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    currency_id = fields.Many2one(related='job_id.currency_id', store=True, readonly=True)
    quantity = fields.Float(default=1.0)
    product_code = fields.Char('Product Code')
    product_code_raw = fields.Char('Raw Product Code')
    # Brand/manufacturer parsed from the line text (not the invoice vendor partner).
    supplier_brand = fields.Char('Brand')
    supplier_brand_id = fields.Integer('Brand Supplier ID')
    product_description = fields.Char('Description')
    unit_price = fields.Monetary(string='PU fara TVA', currency_field='currency_id')
    discount_percent = fields.Float(string='Reducere %', default=0.0)
    discounted_unit_price = fields.Monetary(
        string='Pret unit. disc.',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    unit_price_incl_vat = fields.Monetary(
        string='PU cu TVA',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    vat_rate = fields.Float(string='TVA %')
    vat_unit_amount = fields.Monetary(
        string='TVA pe unit.',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    subtotal = fields.Monetary(
        string='Total fara TVA',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    subtotal_incl_vat = fields.Monetary(
        string='Total cu TVA',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    markup_percent = fields.Float(string='Adaos %', default=lambda self: self._default_markup_percent())
    markup_amount = fields.Monetary(
        string='Adaos',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    sale_price_excl_vat = fields.Monetary(
        string='Pret vanzare fara TVA',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    sale_price_incl_vat = fields.Monetary(
        string='Pret unit. cu TVA',
        compute='_compute_financials',
        currency_field='currency_id',
        store=True,
    )
    product_id = fields.Many2one('product.product', string='Matched Product')
    matched_ean = fields.Char(related='product_id.barcode', string='EAN', readonly=True)
    matched_internal_code = fields.Char(
        related='product_id.default_code',
        string='Cod Intern',
        readonly=True,
    )
    match_method = fields.Char('Match Method')
    match_confidence = fields.Float('Match Confidence (%)')
    match_status = fields.Selection(
        [
            ('matched', 'Matched'),
            ('not_found', 'Not Found'),
            ('manual', 'Manual'),
        ],
        compute='_compute_match_status',
        store=True,
    )

    @api.model
    def _default_markup_percent(self):
        raw = (
            os.getenv('INVOICE_INGEST_DEFAULT_MARKUP_PERCENT')
            or self.env['ir.config_parameter'].sudo().get_param('automotive.invoice_ingest_default_markup_percent')
            or '25'
        )
        try:
            return float(raw)
        except Exception:
            return 25.0

    @api.depends('quantity', 'unit_price', 'discount_percent', 'vat_rate', 'markup_percent')
    def _compute_financials(self):
        for line in self:
            quantity = line.quantity or 0.0
            unit_price = line.unit_price or 0.0
            discount_pct = line.discount_percent or 0.0
            vat_pct = line.vat_rate or 0.0
            markup_pct = line.markup_percent or 0.0

            discounted_unit = unit_price * (1 - (discount_pct / 100.0))
            vat_unit = discounted_unit * (vat_pct / 100.0)
            subtotal_excl = quantity * discounted_unit
            subtotal_incl = subtotal_excl + (quantity * vat_unit)
            markup_value = discounted_unit * (markup_pct / 100.0)
            sale_excl = discounted_unit + markup_value
            sale_incl = sale_excl * (1 + (vat_pct / 100.0))

            line.discounted_unit_price = discounted_unit
            line.unit_price_incl_vat = discounted_unit + vat_unit
            line.vat_unit_amount = vat_unit
            line.subtotal = subtotal_excl
            line.subtotal_incl_vat = subtotal_incl
            line.markup_amount = markup_value
            line.sale_price_excl_vat = sale_excl
            line.sale_price_incl_vat = sale_incl

    @api.depends('product_id', 'product_code', 'match_method', 'match_confidence')
    def _compute_match_status(self):
        for line in self:
            if line.product_id:
                method = (line.match_method or '').lower()
                confidence = line.match_confidence or 0.0
                if method.startswith('exact:') or (method.startswith('lookup') and confidence >= 90.0):
                    line.match_status = 'matched'
                else:
                    line.match_status = 'manual'
            else:
                line.match_status = 'not_found'

    @api.onchange('product_code')
    def _onchange_product_code(self):
        for line in self:
            if line.product_id or not line.product_code or not line.job_id:
                continue
            parsed = line.job_id._parse_invoice_line_identity(
                line.product_code,
                product_description=line.product_description,
                supplier_hint=line.supplier_brand,
            )
            if parsed.get('product_code_primary'):
                line.product_code = parsed['product_code_primary']
            if parsed.get('supplier_brand'):
                line.supplier_brand = parsed['supplier_brand']
            if not line.product_code_raw:
                line.product_code_raw = parsed.get('product_code_raw') or line.product_code
            product, meta = line.job_id._match_product_with_meta(
                line.product_code,
                supplier=line.job_id.partner_id,
                product_description=line.product_description,
                supplier_brand=line.supplier_brand,
                extra_codes=parsed.get('code_candidates') or [],
            )
            if product:
                line.product_id = product.id
                line.match_method = meta.get('method')
                line.match_confidence = meta.get('confidence', 0.0)
                canonical_brand, canonical_supplier_id = line.job_id._brand_from_matched_product(product)
                if canonical_brand:
                    line.supplier_brand = canonical_brand
                line.supplier_brand_id = canonical_supplier_id or False

    @api.onchange('job_id')
    def _onchange_job_id_defaults(self):
        for line in self:
            if line.job_id and not line.vat_rate:
                line.vat_rate = line.job_id.vat_rate or 0.0

    @api.onchange('product_id')
    def _onchange_product_id_brand(self):
        for line in self:
            if not line.job_id or not line.product_id:
                continue
            canonical_brand, canonical_supplier_id = line.job_id._brand_from_matched_product(line.product_id)
            if canonical_brand:
                line.supplier_brand = canonical_brand
            line.supplier_brand_id = canonical_supplier_id or False

    def action_try_match(self):
        for line in self:
            if not line.job_id:
                continue
            parsed = line.job_id._parse_invoice_line_identity(
                line.product_code_raw or line.product_code,
                product_description=line.product_description,
                supplier_hint=line.supplier_brand,
            )
            parsed_code = parsed.get('product_code_primary') or line.product_code
            parsed_supplier_brand = parsed.get('supplier_brand') or line.supplier_brand
            product, meta = line.job_id._match_product_with_meta(
                parsed_code,
                supplier=line.job_id.partner_id,
                product_description=line.product_description,
                supplier_brand=parsed_supplier_brand,
                extra_codes=parsed.get('code_candidates') or [],
            )
            if not product and parsed_code:
                progressive_candidates = line.job_id._progressive_tail_trim_candidates(parsed_code)
                if progressive_candidates:
                    parsed_code = progressive_candidates[-1]
            product_id = (
                product.id
                if product and meta.get('confidence', 0.0) >= AUTO_MATCH_CONFIDENCE_THRESHOLD
                else False
            )
            canonical_brand = parsed_supplier_brand
            canonical_supplier_id = False
            if product_id:
                canonical_brand, canonical_supplier_id = line.job_id._brand_from_matched_product(product)
                canonical_brand = canonical_brand or parsed_supplier_brand
            line.write({
                'product_code_raw': line.product_code_raw or parsed.get('product_code_raw') or line.product_code,
                'product_code': parsed_code,
                'supplier_brand': canonical_brand,
                'supplier_brand_id': canonical_supplier_id or False,
                'product_id': product_id,
                'match_method': meta.get('method'),
                'match_confidence': meta.get('confidence', 0.0),
            })
        return True

    def action_clear_match(self):
        self.write({
            'product_id': False,
            'supplier_brand_id': False,
            'match_method': False,
            'match_confidence': 0.0,
        })
        return True


class InvoiceIngestUploadWizard(models.TransientModel):
    _name = 'invoice.ingest.upload.wizard'
    _description = 'Invoice Ingest Upload Wizard'

    pdf_file = fields.Binary('PDF File', required=True)
    pdf_filename = fields.Char('Filename')
    supplier_id = fields.Many2one('res.partner', string='Supplier (Optional)')
    ai_model = fields.Char(
        string='AI Model',
        default=lambda self: self.env['invoice.ingest.job']._default_ai_model(),
    )
    auto_extract = fields.Boolean(
        string='Run AI Extraction Immediately',
        default=True,
        help='If enabled, the system will run OpenAI extraction right after upload.',
    )

    def action_import_pdf(self):
        self.ensure_one()
        if not self.pdf_file:
            raise UserError('Please upload a PDF first.')
        filename = (self.pdf_filename or 'invoice.pdf').strip()
        mimetype = 'application/pdf' if filename.lower().endswith('.pdf') else 'application/octet-stream'

        job = self.env['invoice.ingest.job'].create({
            'name': f'OCR - {filename}',
            'source': 'ocr',
            'state': 'pending',
            'partner_id': self.supplier_id.id if self.supplier_id else False,
            'ai_model': self.ai_model or self.env['invoice.ingest.job']._default_ai_model(),
        })

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': self.pdf_file,
            'mimetype': mimetype,
            'res_model': 'invoice.ingest.job',
            'res_id': job.id,
        })
        job.write({'attachment_id': attachment.id})

        if self.auto_extract:
            try:
                job.action_extract_with_openai()
            except Exception as exc:  # noqa: BLE001
                job.write({
                    'state': 'failed',
                    'error': f'AI extraction failed after upload: {exc}',
                })

        return {
            'name': 'Invoice Ingest Job',
            'type': 'ir.actions.act_window',
            'res_model': 'invoice.ingest.job',
            'res_id': job.id,
            'view_mode': 'form',
            'target': 'current',
        }
