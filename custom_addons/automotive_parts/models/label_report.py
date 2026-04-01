# -*- coding: utf-8 -*-
from odoo import _, models
from odoo.exceptions import UserError


class ReportAutomotiveLabel(models.AbstractModel):
    _name = 'report.automotive_parts.report_product_label'
    _description = 'Automotive Product Label Report'

    @staticmethod
    def _format_price(price):
        try:
            value = float(price or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        formatted = f"{value:.2f}".rstrip('0').rstrip('.')
        return formatted or '0'

    def _build_labels_from_records(self, records):
        ProductProduct = self.env['product.product']
        labels = []
        for record in records:
            product = record
            if record._name == 'product.template':
                product = record.product_variant_id
            if not product or product._name != 'product.product':
                continue
            labels.append(ProductProduct._prepare_label_payload_from_values(
                name=product.name,
                barcode=product.barcode or product.barcode_internal,
                product_code=product.supplier_code or product.default_code or product.tecdoc_article_no,
                internal_code=product.default_code,
                price=product.lst_price or product.product_tmpl_id.list_price or 0.0,
                brand=product.tecdoc_supplier_name or product.main_supplier_id.name,
            ))
        return labels

    def _get_report_values(self, docids, data=None):
        labels = list((data or {}).get('labels') or [])
        if not labels:
            active_model = self.env.context.get('active_model') or 'product.template'
            if active_model in {'product.template', 'product.product'} and docids:
                labels = self._build_labels_from_records(self.env[active_model].browse(docids).exists())
        if not labels:
            raise UserError(_('No labels were prepared for printing.'))
        return {
            'doc_ids': docids,
            'doc_model': self.env.context.get('active_model') or 'product.template',
            'docs': labels,
            'company': self.env.company,
            'format_price': self._format_price,
        }
