# -*- coding: utf-8 -*-
import re
from time import monotonic

from odoo import api, models
from odoo.exceptions import UserError
from odoo.osv import expression

from ..runtime_logging import emit_runtime_event
from .invoice_ingest_code_utils import (
    allow_progressive_tail_trim_name,
    build_openai_extraction_prompt,
    code_candidates,
    compact_code,
    extract_primary_code,
    extract_supplier_brand,
    is_supplier_token,
    normalize_code_value,
    parse_invoice_line_identity,
    progressive_tail_trim_candidates,
    trimmed_code_variants,
)
from .invoice_ingest_shared import (
    AUTO_MATCH_CONFIDENCE_THRESHOLD,
    _logger,
)


class InvoiceIngestJobMatch(models.Model):
    _inherit = 'invoice.ingest.job'

    @api.model
    def _format_match_trace_message(self, line_index=None, line_total=None, detail=''):
        if line_index and line_total:
            return f'Line {line_index}/{line_total}: {detail}'
        if line_index:
            return f'Line {line_index}: {detail}'
        return detail

    def _emit_match_runtime_event(
        self,
        phase,
        detail,
        line_index=None,
        line_total=None,
        raw_code='',
        product_code='',
        product_description='',
        supplier=None,
        supplier_brand='',
        match_meta=None,
        matched_product=None,
        elapsed_ms=None,
        extra=None,
    ):
        self.ensure_one()
        if getattr(self.env, 'in_onchange', False):
            return
        async_job = self._get_context_async_job()
        supplier_id = False
        if isinstance(supplier, models.BaseModel):
            supplier = supplier[:1]
            supplier_id = supplier.id if supplier and supplier._name == 'res.partner' else False
        elif supplier:
            try:
                supplier_id = int(supplier)
            except Exception:
                supplier_id = False

        payload = {
            'event': 'invoice_ingest_match_trace',
            'level': 'info',
            'category': 'invoice_ingest',
            'source': 'invoice.ingest.job',
            'outcome': 'progress',
            'db': self.env.cr.dbname,
            'uid': self.env.uid,
            'job_type': 'invoice_ingest',
            'job_id': async_job.id if async_job else False,
            'related_model': self._name,
            'related_res_id': self.id,
            'phase': phase,
            'message': self._format_match_trace_message(line_index=line_index, line_total=line_total, detail=detail),
            'line_index': line_index or False,
            'line_total': line_total or False,
            'raw_code': raw_code or '',
            'product_code': product_code or '',
            'product_description': product_description or '',
            'supplier_id': supplier_id or False,
            'supplier_brand': supplier_brand or '',
        }
        if matched_product:
            payload.update({
                'matched_product_id': matched_product.id,
                'matched_product_name': matched_product.display_name,
            })
        if match_meta:
            payload.update({
                'match_method': match_meta.get('method'),
                'match_confidence': match_meta.get('confidence'),
                'matched_code': match_meta.get('matched_code'),
            })
        if elapsed_ms is not None:
            payload['elapsed_ms'] = round(float(elapsed_ms), 2)
        if extra:
            payload.update(extra)
        persist_db = bool(
            self.env.context.get('automotive_async_processing')
            or self.env.context.get('automotive_async_job_id')
        )
        emit_runtime_event(payload, persist_db=persist_db)

    @api.model
    def _normalize_code_value(self, value):
        return normalize_code_value(value)

    @api.model
    def _compact_code(self, value):
        return compact_code(value)

    @api.model
    def _is_supplier_token(self, token):
        return is_supplier_token(token)

    @api.model
    def _extract_supplier_brand(self, raw_text, supplier_hint=None):
        return extract_supplier_brand(raw_text, supplier_hint=supplier_hint)

    @api.model
    def _extract_primary_code(self, raw_text):
        return extract_primary_code(raw_text)

    @api.model
    def _trimmed_code_variants(self, code):
        return trimmed_code_variants(code)

    @api.model
    def _progressive_tail_trim_candidates(self, code):
        return progressive_tail_trim_candidates(code)

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
        return allow_progressive_tail_trim_name(supplier_name)

    @api.model
    def _build_openai_extraction_prompt(self, supplier_name_hint=''):
        return build_openai_extraction_prompt(supplier_name_hint)

    @api.model
    def _code_candidates(self, value, extra=None):
        return code_candidates(value, extra=extra)

    @api.model
    def _parse_invoice_line_identity(self, product_code_raw, product_description='', supplier_hint=''):
        return parse_invoice_line_identity(
            product_code_raw,
            product_description=product_description,
            supplier_hint=supplier_hint,
        )

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
    def _product_from_template_candidates(self, template_ids, scopes):
        Product = self.env['product.product']
        template_ids = list(dict.fromkeys(template_ids or []))
        if not template_ids:
            return Product, ''
        return self._search_product_with_scopes(
            [('product_tmpl_id', 'in', template_ids)],
            scopes,
        )

    @api.model
    def _catalog_template_candidates_by_key(self, key):
        ProductTemplate = self.env['product.template']
        Variant = self.env['tecdoc.article.variant']
        Ean = self.env['tecdoc.article.variant.ean']
        Oem = self.env['tecdoc.oem.number']
        CrossNumber = self.env['tecdoc.cross.number']
        CrossLink = self.env['tecdoc.article.variant.cross']

        direct_templates = ProductTemplate.search(
            [('tecdoc_article_no_key', '=', key)],
            limit=25,
        )
        if direct_templates:
            yield direct_templates.ids

        variants = Variant.search(
            [('article_no_key', '=', key), ('product_tmpl_id', '!=', False)],
            limit=25,
        )
        if variants:
            yield variants.mapped('product_tmpl_id').ids

        eans = Ean.search([('ean_key', '=', key)], limit=25)
        if eans:
            yield eans.mapped('variant_id.product_tmpl_id').ids

        oem_numbers = Oem.search([('number_key', '=', key)], limit=25)
        if oem_numbers:
            variants = Variant.search(
                [('oem_number_ids', 'in', oem_numbers.ids), ('product_tmpl_id', '!=', False)],
                limit=50,
            )
            if variants:
                yield variants.mapped('product_tmpl_id').ids

        cross_numbers = CrossNumber.search([('number_key', '=', key)], limit=25)
        if cross_numbers:
            cross_links = CrossLink.search(
                [('cross_number_id', 'in', cross_numbers.ids)],
                limit=50,
            )
            if cross_links:
                yield cross_links.mapped('variant_id.product_tmpl_id').ids

    @api.model
    def _match_by_catalog_lookup(self, code, supplier_domain=None, supplier_brand_domain=None):
        Product = self.env['product.product']
        key = self._compact_code(code)
        if not key:
            return Product, ''

        scopes = self._product_search_scopes(
            supplier_domain=supplier_domain,
            supplier_brand_domain=supplier_brand_domain,
        )
        for template_ids in self._catalog_template_candidates_by_key(key):
            product, reason_suffix = self._product_from_template_candidates(template_ids, scopes)
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

        api = self.env['tecdoc.api']._get_default_api()
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
                product = api.sync_product_from_tecdoc(
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
        line_index=None,
        line_total=None,
        prefer_parsed_code=False,
    ):
        self.ensure_one()
        if line_index is None:
            line_index = self.env.context.get('invoice_ingest_match_line_index')
        if line_total is None:
            line_total = self.env.context.get('invoice_ingest_match_line_total')
        line_started_at = monotonic()
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
        visible_code = parsed_code if prefer_parsed_code and parsed_code else (exact_code or parsed_code)
        candidate_codes = list(parsed_identity.get('code_candidates') or [])
        if exact_code and exact_code not in candidate_codes:
            candidate_codes.insert(0, exact_code)
        for code in extra_codes or []:
            normalized_code = self._normalize_code_value(code)
            if normalized_code and normalized_code not in candidate_codes:
                candidate_codes.append(normalized_code)

        self._emit_match_runtime_event(
            phase='line_start',
            detail='starting local product matching',
            line_index=line_index,
            line_total=line_total,
            raw_code=raw_code,
            product_code=product_code or visible_code,
            product_description=product_description,
            supplier=supplier,
            supplier_brand=parsed_supplier_brand,
            extra={
                'candidate_code_count': len(candidate_codes),
                'allow_progressive_trim': use_trimmed_visible_code,
            },
        )
        product, match_meta = self._match_product_with_meta(
            parsed_code,
            supplier=supplier,
            product_description=product_description,
            supplier_brand=parsed_supplier_brand,
            extra_codes=candidate_codes,
            raw_code=raw_code,
            line_index=line_index,
            line_total=line_total,
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

        self._emit_match_runtime_event(
            phase='line_complete',
            detail='finished local product matching',
            line_index=line_index,
            line_total=line_total,
            raw_code=raw_code,
            product_code=visible_code or product_code,
            product_description=product_description,
            supplier=supplier,
            supplier_brand=parsed_supplier_brand,
            match_meta=match_meta,
            matched_product=matched_product,
            elapsed_ms=(monotonic() - line_started_at) * 1000.0,
            extra={
                'match_status': 'matched' if matched_product else 'not_found',
                'supplier_brand_id': supplier_brand_id or False,
            },
        )

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

    def _match_product_with_meta(
        self,
        product_code,
        supplier=None,
        product_description=None,
        supplier_brand=None,
        extra_codes=None,
        raw_code='',
        line_index=None,
        line_total=None,
    ):
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
        self._emit_match_runtime_event(
            phase='exact_start',
            detail='starting strict exact code matching',
            line_index=line_index,
            line_total=line_total,
            raw_code=raw_code,
            product_code=product_code,
            product_description=product_description,
            supplier=supplier,
            supplier_brand=supplier_brand,
            extra={'candidate_code_count': len(codes)},
        )
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
            self._emit_match_runtime_event(
                phase='matched',
                detail=f"matched during {match_meta.get('method') or 'exact'}",
                line_index=line_index,
                line_total=line_total,
                raw_code=raw_code,
                product_code=product_code,
                product_description=product_description,
                supplier=supplier,
                supplier_brand=supplier_brand,
                match_meta=match_meta,
                matched_product=product,
            )
            return product, match_meta

        # 2) Exact TecDoc lookup through variant/oem/ean/cross relations.
        self._emit_match_runtime_event(
            phase='lookup_start',
            detail='starting catalog lookup matching',
            line_index=line_index,
            line_total=line_total,
            raw_code=raw_code,
            product_code=product_code,
            product_description=product_description,
            supplier=supplier,
            supplier_brand=supplier_brand,
        )
        for code in codes:
            product, lookup_reason = self._match_by_catalog_lookup(
                code=code,
                supplier_domain=supplier_domain,
                supplier_brand_domain=supplier_brand_domain,
            )
            if product:
                confidence = 95.0 if 'supplier' in lookup_reason else 84.0
                match_meta = {
                    'method': lookup_reason or 'lookup',
                    'matched_code': code,
                    'confidence': confidence,
                }
                self._emit_match_runtime_event(
                    phase='matched',
                    detail=f"matched during {match_meta.get('method') or 'lookup'}",
                    line_index=line_index,
                    line_total=line_total,
                    raw_code=raw_code,
                    product_code=product_code,
                    product_description=product_description,
                    supplier=supplier,
                    supplier_brand=supplier_brand,
                    match_meta=match_meta,
                    matched_product=product,
                )
                return product, match_meta

        # 2b) Progressive tail-trim pass (only if strict candidates failed).
        trim_candidates = (
            self._progressive_tail_trim_candidates(product_code)
            if self._allow_progressive_tail_trim(supplier)
            else []
        )
        self._emit_match_runtime_event(
            phase='trim_start',
            detail='starting progressive trim matching',
            line_index=line_index,
            line_total=line_total,
            raw_code=raw_code,
            product_code=product_code,
            product_description=product_description,
            supplier=supplier,
            supplier_brand=supplier_brand,
            extra={'trim_candidate_count': len(trim_candidates)},
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
            self._emit_match_runtime_event(
                phase='matched',
                detail=f"matched during {match_meta.get('method') or 'progressive trim'}",
                line_index=line_index,
                line_total=line_total,
                raw_code=raw_code,
                product_code=product_code,
                product_description=product_description,
                supplier=supplier,
                supplier_brand=supplier_brand,
                match_meta=match_meta,
                matched_product=product,
            )
            return product, match_meta

        for code in trim_candidates:
            product, lookup_reason = self._match_by_catalog_lookup(
                code=code,
                supplier_domain=supplier_domain,
                supplier_brand_domain=supplier_brand_domain,
            )
            if product:
                confidence = 82.0 if 'supplier' in lookup_reason else 74.0
                match_meta = {
                    'method': f'progressive_trim:{lookup_reason or "lookup"}',
                    'matched_code': code,
                    'confidence': confidence,
                }
                self._emit_match_runtime_event(
                    phase='matched',
                    detail=f"matched during {match_meta.get('method') or 'progressive trim lookup'}",
                    line_index=line_index,
                    line_total=line_total,
                    raw_code=raw_code,
                    product_code=product_code,
                    product_description=product_description,
                    supplier=supplier,
                    supplier_brand=supplier_brand,
                    match_meta=match_meta,
                    matched_product=product,
                )
                return product, match_meta

        # 3) Relaxed `ilike` only for code-like fields and only when we do have a code.
        self._emit_match_runtime_event(
            phase='ilike_start',
            detail='starting relaxed code matching',
            line_index=line_index,
            line_total=line_total,
            raw_code=raw_code,
            product_code=product_code,
            product_description=product_description,
            supplier=supplier,
            supplier_brand=supplier_brand,
        )
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
            self._emit_match_runtime_event(
                phase='matched',
                detail=f"matched during {match_meta.get('method') or 'ilike'}",
                line_index=line_index,
                line_total=line_total,
                raw_code=raw_code,
                product_code=product_code,
                product_description=product_description,
                supplier=supplier,
                supplier_brand=supplier_brand,
                match_meta=match_meta,
                matched_product=product,
            )
            return product, match_meta

        # 4) Description fallback only when no code was parsed at all.
        description = (product_description or '').strip()
        if description and not codes:
            self._emit_match_runtime_event(
                phase='description_start',
                detail='starting description-only matching',
                line_index=line_index,
                line_total=line_total,
                raw_code=raw_code,
                product_code=product_code,
                product_description=product_description,
                supplier=supplier,
                supplier_brand=supplier_brand,
            )
            product, reason_suffix = self._search_product_with_scopes(
                [('name', '=ilike', ' '.join(description.split()))],
                scopes,
            )
            if product:
                confidence = 70.0 if reason_suffix else 62.0
                match_meta = {
                    'method': f'description_exact{reason_suffix}',
                    'matched_code': '',
                    'confidence': confidence,
                }
                self._emit_match_runtime_event(
                    phase='matched',
                    detail=f"matched during {match_meta.get('method') or 'description'}",
                    line_index=line_index,
                    line_total=line_total,
                    raw_code=raw_code,
                    product_code=product_code,
                    product_description=product_description,
                    supplier=supplier,
                    supplier_brand=supplier_brand,
                    match_meta=match_meta,
                    matched_product=product,
                )
                return product, match_meta

        if codes:
            self._emit_match_runtime_event(
                phase='tecdoc_auto_start',
                detail='starting TecDoc live lookup',
                line_index=line_index,
                line_total=line_total,
                raw_code=raw_code,
                product_code=product_code,
                product_description=product_description,
                supplier=supplier,
                supplier_brand=supplier_brand,
                extra={'candidate_code_count': len(codes)},
            )
            product, match_meta = self._match_or_create_from_tecdoc(codes, supplier_brand=supplier_brand)
            if product:
                self._emit_match_runtime_event(
                    phase='matched',
                    detail=f"matched during {match_meta.get('method') or 'TecDoc live lookup'}",
                    line_index=line_index,
                    line_total=line_total,
                    raw_code=raw_code,
                    product_code=product_code,
                    product_description=product_description,
                    supplier=supplier,
                    supplier_brand=supplier_brand,
                    match_meta=match_meta,
                    matched_product=product,
                )
                return product, match_meta
            self._emit_match_runtime_event(
                phase='tecdoc_auto_miss',
                detail='no TecDoc live match found',
                line_index=line_index,
                line_total=line_total,
                raw_code=raw_code,
                product_code=product_code,
                product_description=product_description,
                supplier=supplier,
                supplier_brand=supplier_brand,
            )

        match_meta = {
            'method': 'not_found',
            'matched_code': '',
            'confidence': 0.0,
        }
        self._emit_match_runtime_event(
            phase='not_found',
            detail='no local product match found',
            line_index=line_index,
            line_total=line_total,
            raw_code=raw_code,
            product_code=product_code,
            product_description=product_description,
            supplier=supplier,
            supplier_brand=supplier_brand,
            match_meta=match_meta,
        )
        return Product, match_meta

    def _replace_lines_from_normalized(self, normalized_lines):
        self.ensure_one()
        commands = [(5, 0, 0)]
        sequence = 1
        for line in normalized_lines or []:
            normalized_line = self._normalized_line_from_payload(line)
            if not normalized_line:
                continue
            description = (normalized_line.get('product_description') or '').strip()
            has_precomputed_match = any(
                key in normalized_line
                for key in ('matched_product_id', 'match_method', 'match_confidence', 'supplier_brand_id')
            ) and bool(normalized_line.get('match_method') or normalized_line.get('matched_product_id'))
            if has_precomputed_match:
                resolved = normalized_line
            else:
                resolved = self._resolve_line_match_data(
                    raw_code=normalized_line.get('product_code_raw') or normalized_line.get('product_code'),
                    product_code=normalized_line.get('product_code'),
                    product_description=description,
                    supplier=self.partner_id,
                    supplier_brand=normalized_line.get('supplier_brand'),
                )
            commands.append((0, 0, {
                'sequence': sequence,
                'quantity': self._safe_float(normalized_line.get('quantity'), default=1.0) or 1.0,
                'product_code_raw': resolved['product_code_raw'],
                'product_code': resolved['product_code'],
                'supplier_brand': resolved['supplier_brand'],
                'supplier_brand_id': resolved['supplier_brand_id'],
                'product_description': description,
                'unit_price': self._safe_float(normalized_line.get('unit_price'), default=0.0),
                'vat_rate': self._safe_float(normalized_line.get('vat_rate'), default=self.vat_rate or 0.0),
                'product_id': resolved['matched_product_id'],
                'match_method': resolved['match_method'],
                'match_confidence': resolved['match_confidence'],
            }))
            sequence += 1
        self.write({'line_ids': commands})
