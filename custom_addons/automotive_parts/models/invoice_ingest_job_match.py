# -*- coding: utf-8 -*-
import re

from odoo import api, models
from odoo.exceptions import UserError
from odoo.osv import expression

from .invoice_ingest import (
    AUTO_MATCH_CONFIDENCE_THRESHOLD,
    INVOICE_CODE_STOP_WORDS,
    INVOICE_META_PREFIXES,
    INVOICE_SUPPLIER_NOISE_TOKENS,
    INVOICE_TRIM_SUFFIXES,
    PROGRESSIVE_TRIM_MAX_STEPS,
    PROGRESSIVE_TRIM_MIN_LEN,
    PROGRESSIVE_TRIM_SUPPLIER_TOKENS,
    _logger,
)


class InvoiceIngestJobMatch(models.Model):
    _inherit = 'invoice.ingest.job'
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
    def _allow_progressive_tail_trim(self, supplier=None):
        supplier_rec = supplier
        if isinstance(supplier_rec, str):
            return self._allow_progressive_tail_trim_name(supplier_rec)
        if not supplier_rec:
            return False
        if not isinstance(supplier_rec, models.BaseModel):
            try:
                supplier_rec = self.env['res.partner'].browse(int(supplier_rec))
            except Exception:
                return False
        supplier_rec = supplier_rec[:1]
        if not supplier_rec or supplier_rec._name != 'res.partner':
            return False
        return self._allow_progressive_tail_trim_name(supplier_rec.name or '')

    @api.model
    def _allow_progressive_tail_trim_name(self, supplier_name=''):
        normalized_name = self._normalize_code_value(supplier_name or '')
        return any(token in normalized_name for token in PROGRESSIVE_TRIM_SUPPLIER_TOKENS)

    @api.model
    def _build_openai_extraction_prompt(self, supplier_name_hint=''):
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
        if self._allow_progressive_tail_trim_name(supplier_name_hint):
            prompt += (
                " Special case for Auto Total invoices: keep the exact printed value in product_code_raw, "
                "but product_code may contain the trimmed main article code when supplier suffix letters are glued "
                "to the end of the printed code."
            )
        return prompt

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
    def _product_search_scopes(self, supplier_domain=None, supplier_brand_domain=None):
        scopes = []
        if supplier_domain:
            scopes.append((supplier_domain, ' supplier'))
        if supplier_brand_domain:
            scopes.append((supplier_brand_domain, ' supplier brand'))
        scopes.append(([], ''))
        return scopes

    @api.model
    def _search_product_with_scopes(self, base_domain, scopes):
        Product = self.env['product.product']
        for extra_domain, reason_suffix in scopes:
            domain = expression.AND([base_domain, extra_domain]) if extra_domain else base_domain
            product = Product.search(domain, limit=1)
            if product:
                return product, reason_suffix
        return Product, ''

    @api.model
    def _search_code_fields_with_scopes(
        self,
        codes,
        field_names,
        scopes,
        operator='=',
        method_prefix='exact',
        confidence_with_scope=100.0,
        confidence_without_scope=96.0,
        min_compact_len=0,
    ):
        Product = self.env['product.product']
        for field_name in field_names:
            for code in codes:
                if min_compact_len and len(self._compact_code(code)) < min_compact_len:
                    continue
                product, reason_suffix = self._search_product_with_scopes(
                    [(field_name, operator, code)],
                    scopes,
                )
                if product:
                    return product, {
                        'method': f'{method_prefix}:{field_name}{reason_suffix}',
                        'matched_code': code,
                        'confidence': confidence_with_scope if reason_suffix else confidence_without_scope,
                    }
        return Product, {}

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
        product, reason_suffix = self._search_product_with_scopes(
            lookup_domain,
            self._product_search_scopes(
                supplier_domain=supplier_domain,
                supplier_brand_domain=supplier_brand_domain,
            ),
        )
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

    @api.model
    def _normalize_tecdoc_supplier_key(self, value):
        return re.sub(r'[^0-9A-Z]+', '', (value or '').strip().upper())

    def _guess_tecdoc_supplier_id(self, supplier_brand=None):
        self.ensure_one()
        brand = (supplier_brand or '').strip()
        if not brand or brand.upper() == 'UNKNOWN':
            return False

        Supplier = self.env['tecdoc.supplier'].sudo()
        key = self._normalize_tecdoc_supplier_key(brand)
        if not key:
            return False

        supplier = (
            Supplier.search([('supplier_match_code', '=', key)], limit=1)
            or Supplier.search([('name', '=ilike', brand)], limit=1)
            or Supplier.search([('name', '=ilike', key)], limit=1)
        )
        return supplier.supplier_id if supplier else False

    def _match_or_create_from_tecdoc(self, codes, supplier_brand=None):
        self.ensure_one()
        if self.source != 'ocr':
            return self.env['product.product'], {}

        api = self.env['tecdoc.api'].sudo().search([], limit=1)
        if not api:
            return self.env['product.product'], {}

        supplier_id = self._guess_tecdoc_supplier_id(supplier_brand)
        attempted = set()
        for code in codes or []:
            normalized_code = self._normalize_code_value(code)
            compact_code = self._compact_code(normalized_code)
            if not normalized_code or len(compact_code) < 4 or compact_code in attempted:
                continue
            attempted.add(compact_code)
            try:
                product = api.with_context(tecdoc_single_attempt=True).sync_product_from_tecdoc(
                    article_no=normalized_code,
                    supplier_id=supplier_id or None,
                )
            except UserError as exc:
                _logger.info(
                    "Invoice ingest TecDoc auto-sync miss for code=%s supplier_brand=%s supplier_id=%s: %s",
                    normalized_code,
                    supplier_brand or '',
                    supplier_id or False,
                    exc,
                )
                continue
            product = product if product._name == 'product.product' else product.product_variant_id
            if product:
                return product, {
                    'method': 'exact:tecdoc_auto_sync',
                    'matched_code': normalized_code,
                    'confidence': 94.0,
                }

        return self.env['product.product'], {}

    def _resolve_line_match_data(
        self,
        raw_code='',
        product_code='',
        product_description='',
        supplier=None,
        supplier_brand='',
        extra_codes=None,
    ):
        self.ensure_one()
        raw_code = (
            raw_code
            or product_code
            or product_description
            or ''
        ).strip()
        product_code = (product_code or '').strip()
        product_description = (product_description or '').strip()
        supplier_brand = (supplier_brand or '').strip()

        parsed_identity = self._parse_invoice_line_identity(
            raw_code,
            product_description=product_description,
            supplier_hint=supplier_brand,
        )
        exact_code = self._normalize_code_value(product_code or raw_code)
        parsed_code = parsed_identity.get('product_code_primary') or exact_code
        parsed_supplier_brand = parsed_identity.get('supplier_brand') or supplier_brand

        use_trimmed_visible_code = self._allow_progressive_tail_trim(supplier)
        visible_code = exact_code or parsed_code
        candidate_codes = list(parsed_identity.get('code_candidates') or [])
        if exact_code and exact_code not in candidate_codes:
            candidate_codes.insert(0, exact_code)
        for code in extra_codes or []:
            normalized_code = self._normalize_code_value(code)
            if normalized_code and normalized_code not in candidate_codes:
                candidate_codes.append(normalized_code)

        product, match_meta = self._match_product_with_meta(
            parsed_code,
            supplier=supplier,
            product_description=product_description,
            supplier_brand=parsed_supplier_brand,
            extra_codes=candidate_codes,
        )
        if use_trimmed_visible_code:
            trim_candidates = self._progressive_tail_trim_candidates(exact_code or parsed_code)
            if trim_candidates:
                visible_code = trim_candidates[-1]
        matched_product = (
            product
            if product and match_meta.get('confidence', 0.0) >= AUTO_MATCH_CONFIDENCE_THRESHOLD
            else self.env['product.product']
        )
        supplier_brand_id = False
        if matched_product:
            canonical_brand, canonical_supplier_id = self._brand_from_matched_product(matched_product)
            if canonical_brand:
                parsed_supplier_brand = canonical_brand
            supplier_brand_id = canonical_supplier_id or False

        return {
            'product_code_raw': raw_code,
            'product_code': visible_code or False,
            'supplier_brand': parsed_supplier_brand,
            'supplier_brand_id': supplier_brand_id,
            'matched_product_id': matched_product.id if matched_product else False,
            'matched_product_name': matched_product.display_name if matched_product else False,
            'match_status': 'matched' if matched_product else 'not_found',
            'match_method': match_meta.get('method'),
            'match_confidence': match_meta.get('confidence', 0.0),
        }

    def _match_product_with_meta(self, product_code, supplier=None, product_description=None, supplier_brand=None, extra_codes=None):
        self.ensure_one()
        Product = self.env['product.product']
        supplier_domain = self._supplier_product_domain(supplier)
        supplier_brand_domain = self._supplier_brand_domain(supplier_brand)
        codes = self._code_candidates(product_code, extra=extra_codes)
        scopes = self._product_search_scopes(
            supplier_domain=supplier_domain,
            supplier_brand_domain=supplier_brand_domain,
        )

        # 1) Strict exact matching by code fields; try constrained scopes first.
        # Prefer article-based matching first (TecDoc articleNo / internal references),
        # then fall back to supplier/barcode fields.
        product, match_meta = self._search_code_fields_with_scopes(
            codes=codes,
            field_names=('tecdoc_article_no', 'default_code', 'supplier_code', 'barcode_internal', 'barcode'),
            scopes=scopes,
            operator='=',
            method_prefix='exact',
            confidence_with_scope=100.0,
            confidence_without_scope=96.0,
        )
        if product:
            return product, match_meta

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
        trim_candidates = (
            self._progressive_tail_trim_candidates(product_code)
            if self._allow_progressive_tail_trim(supplier)
            else []
        )
        product, match_meta = self._search_code_fields_with_scopes(
            codes=trim_candidates,
            field_names=('tecdoc_article_no', 'default_code', 'supplier_code', 'barcode_internal', 'barcode'),
            scopes=scopes,
            operator='=',
            method_prefix='progressive_trim',
            confidence_with_scope=83.0,
            confidence_without_scope=75.0,
        )
        if product:
            return product, match_meta

        for code in trim_candidates:
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
        product, match_meta = self._search_code_fields_with_scopes(
            codes=codes,
            field_names=('tecdoc_article_no', 'default_code', 'supplier_code'),
            scopes=scopes,
            operator='=ilike',
            method_prefix='ilike',
            confidence_with_scope=86.0,
            confidence_without_scope=80.0,
            min_compact_len=4,
        )
        if product:
            return product, match_meta

        # 4) Description fallback only when no code was parsed at all.
        description = (product_description or '').strip()
        if description and not codes:
            product, reason_suffix = self._search_product_with_scopes(
                [('name', '=ilike', ' '.join(description.split()))],
                scopes,
            )
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
            description = (line.get('product_description') or '').strip()
            has_precomputed_match = any(
                key in line
                for key in (
                    'matched_product_id',
                    'match_method',
                    'match_confidence',
                    'supplier_brand_id',
                )
            )
            if has_precomputed_match:
                resolved = {
                    'product_code_raw': line.get('product_code_raw') or line.get('product_code') or False,
                    'product_code': line.get('product_code') or False,
                    'supplier_brand': line.get('supplier_brand') or '',
                    'supplier_brand_id': line.get('supplier_brand_id') or False,
                    'matched_product_id': line.get('matched_product_id') or False,
                    'match_method': line.get('match_method') or False,
                    'match_confidence': self._safe_float(line.get('match_confidence'), default=0.0),
                }
            else:
                resolved = self._resolve_line_match_data(
                    raw_code=line.get('product_code_raw') or line.get('product_code'),
                    product_code=line.get('product_code'),
                    product_description=description,
                    supplier=self.partner_id,
                    supplier_brand=line.get('supplier_brand'),
                )
            commands.append((0, 0, {
                'sequence': sequence,
                'quantity': self._safe_float(line.get('quantity'), default=1.0) or 1.0,
                'product_code_raw': resolved['product_code_raw'],
                'product_code': resolved['product_code'],
                'supplier_brand': resolved['supplier_brand'],
                'supplier_brand_id': resolved['supplier_brand_id'],
                'product_description': description,
                'unit_price': self._safe_float(line.get('unit_price'), default=0.0),
                'vat_rate': self._safe_float(line.get('vat_rate'), default=self.vat_rate or 0.0),
                'product_id': resolved['matched_product_id'],
                'match_method': resolved['match_method'],
                'match_confidence': resolved['match_confidence'],
            }))
            sequence += 1
        self.write({'line_ids': commands})
