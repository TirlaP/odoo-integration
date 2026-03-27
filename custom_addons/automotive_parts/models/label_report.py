# -*- coding: utf-8 -*-
from odoo import _, models
from odoo.exceptions import UserError


class ReportAutomotiveLabel(models.AbstractModel):
    _name = 'report.automotive_parts.report_product_label'
    _description = 'Automotive Product Label Report'

    def _get_report_values(self, docids, data=None):
        labels = list((data or {}).get('labels') or [])
        if not labels:
            raise UserError(_('No labels were prepared for printing.'))
        return {
            'doc_ids': docids,
            'doc_model': 'invoice.ingest.job.line',
            'docs': labels,
            'company': self.env.company,
        }
