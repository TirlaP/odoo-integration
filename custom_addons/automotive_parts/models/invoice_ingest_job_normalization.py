# -*- coding: utf-8 -*-
from odoo import models


class InvoiceIngestJobNormalization(models.Model):
    _inherit = 'invoice.ingest.job'

    def _build_normalized_invoice_line(
        self,
        *,
        quantity=1.0,
        product_description='',
        unit_price=0.0,
        vat_rate=0.0,
        resolved=None,
    ):
        self.ensure_one()
        resolved = dict(resolved or {})
        return {
            'quantity': quantity,
            'product_description': (product_description or '').strip(),
            'unit_price': unit_price,
            'vat_rate': vat_rate,
            'product_code_raw': resolved.get('product_code_raw') or False,
            'product_code': resolved.get('product_code') or False,
            'supplier_brand': resolved.get('supplier_brand') or '',
            'supplier_brand_id': resolved.get('supplier_brand_id') or False,
            'matched_product_id': resolved.get('matched_product_id') or False,
            'matched_product_name': resolved.get('matched_product_name') or False,
            'match_status': resolved.get('match_status') or 'not_found',
            'match_method': resolved.get('match_method') or False,
            'match_confidence': self._safe_float(resolved.get('match_confidence'), default=0.0),
        }

    def _normalize_payload_line(self, line, supplier=None, default_vat_rate=0.0):
        self.ensure_one()
        if not isinstance(line, dict):
            return False

        description = (line.get('product_description') or line.get('description') or '').strip()
        resolved = self._resolve_line_match_data(
            raw_code=(
                line.get('product_code_raw')
                or line.get('product_code')
                or line.get('description')
                or line.get('product_description')
                or ''
            ),
            product_code=line.get('product_code'),
            product_description=description,
            supplier=supplier,
            supplier_brand=line.get('supplier_brand'),
        )

        quantity = self._safe_float(
            line.get('quantity') or line.get('invoiced_quantity') or line.get('credited_quantity'),
            default=1.0,
        ) or 1.0
        unit_price = self._safe_float(
            line.get('unit_price')
            or line.get('price_unit')
            or line.get('price'),
            default=0.0,
        )
        line_total = self._safe_float(line.get('line_total'), default=0.0)
        if not unit_price and line_total and quantity:
            unit_price = line_total / quantity

        return self._build_normalized_invoice_line(
            quantity=quantity,
            product_description=description,
            unit_price=unit_price,
            vat_rate=self._safe_float(line.get('vat_rate'), default=default_vat_rate or self.vat_rate or 0.0),
            resolved=resolved,
        )

    def _normalized_line_from_job_line(self, line):
        self.ensure_one()
        return self._build_normalized_invoice_line(
            quantity=line.quantity or 1.0,
            product_description=line.product_description or '',
            unit_price=line.discounted_unit_price,
            vat_rate=line.vat_rate or self.vat_rate or 0.0,
            resolved={
                'product_code_raw': line.product_code_raw or line.product_code,
                'product_code': line.product_code,
                'supplier_brand': line.supplier_brand,
                'supplier_brand_id': line.supplier_brand_id,
                'matched_product_id': line.product_id.id if line.product_id else False,
                'matched_product_name': line.product_id.display_name if line.product_id else False,
                'match_status': line.match_status,
                'match_method': line.match_method,
                'match_confidence': line.match_confidence,
            },
        )

    def _normalized_line_from_payload(self, line):
        self.ensure_one()
        if not isinstance(line, dict):
            return False
        description = (
            (line.get('product_description') or '').strip()
            or (line.get('description') or '').strip()
        )
        resolved = {
            'product_code_raw': line.get('product_code_raw') or line.get('product_code') or False,
            'product_code': line.get('product_code') or False,
            'supplier_brand': line.get('supplier_brand') or '',
            'supplier_brand_id': line.get('supplier_brand_id') or False,
            'matched_product_id': line.get('matched_product_id') or False,
            'matched_product_name': line.get('matched_product_name') or False,
            'match_status': line.get('match_status') or ('matched' if line.get('matched_product_id') else 'not_found'),
            'match_method': line.get('match_method') or False,
            'match_confidence': line.get('match_confidence', 0.0),
        }
        return self._build_normalized_invoice_line(
            quantity=self._safe_float(line.get('quantity'), default=1.0) or 1.0,
            product_description=description,
            unit_price=self._safe_float(
                line.get('unit_price') or line.get('price_unit') or line.get('price'),
                default=0.0,
            ),
            vat_rate=self._safe_float(line.get('vat_rate'), default=self.vat_rate or 0.0),
            resolved=resolved,
        )

    def _iter_effective_normalized_lines(self):
        self.ensure_one()
        if self.line_ids:
            return [self._normalized_line_from_job_line(line) for line in self.line_ids.sorted('sequence')]
        normalized_payload = self._get_normalized_invoice_payload().get('invoice_lines', [])
        return [
            normalized_line
            for normalized_line in (
                self._normalized_line_from_payload(line)
                for line in normalized_payload
            )
            if normalized_line
        ]
