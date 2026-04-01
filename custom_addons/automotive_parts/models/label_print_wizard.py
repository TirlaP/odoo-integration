# -*- coding: utf-8 -*-
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AutomotiveLabelPrintWizard(models.TransientModel):
    _name = 'automotive.label.print.wizard'
    _description = 'Automotive Label Print Wizard'

    source_model = fields.Char(readonly=True)
    source_res_id = fields.Integer(readonly=True)
    source_display_name = fields.Char(readonly=True)
    label_payload_json = fields.Text(string='Label Payload JSON', required=True)
    label_count = fields.Integer(
        string='Quantity',
        default=1,
        help='How many times the prepared label set should be repeated.',
    )
    copies = fields.Integer(
        string='Copies',
        default=1,
        help='How many copies the printer should produce for each generated PDF.',
    )
    output_mode = fields.Selection(
        [
            ('preview_pdf', 'Preview PDF'),
            ('queue_print', 'Queue Print'),
        ],
        string='Output Mode',
        default='queue_print',
        required=True,
    )
    printer_name = fields.Char(string='Printer')
    print_command = fields.Char(string='Print Command')
    job_name = fields.Char(string='Job Name', default=lambda self: _('Automotive labels'))
    total_labels = fields.Integer(
        string='Total Labels',
        compute='_compute_total_labels',
        readonly=True,
    )

    @api.depends('label_payload_json', 'label_count')
    def _compute_total_labels(self):
        for wizard in self:
            labels = wizard._get_base_labels()
            base_total = 0
            for label in labels:
                try:
                    base_total += max(int(label.get('qty') or 1), 1)
                except (TypeError, ValueError, AttributeError):
                    base_total += 1
            wizard.total_labels = base_total * max(int(wizard.label_count or 1), 1)

    def _get_base_labels(self):
        self.ensure_one()
        try:
            payload = json.loads(self.label_payload_json or '[]')
        except Exception as exc:  # noqa: BLE001 - user input should be normalised into a friendly error.
            raise UserError(_('The label payload is invalid: %s') % exc) from exc
        if not isinstance(payload, list):
            raise UserError(_('The label payload must be a list of label dictionaries.'))
        labels = [label for label in payload if isinstance(label, dict) and label.get('barcode')]
        if not labels:
            raise UserError(_('No valid labels were prepared for printing.'))
        return labels

    @api.model
    def open_wizard(
        self,
        *,
        labels,
        source_record=None,
        label_count=1,
        copies=1,
        job_name=False,
        printer_name=False,
        print_command=False,
    ):
        labels = [label for label in (labels or []) if label and label.get('barcode')]
        if not labels:
            raise UserError(_('No valid labels were prepared for printing.'))
        source_record = source_record.exists() if source_record else False
        icp = self.env['ir.config_parameter'].sudo()
        default_output_mode = 'queue_print' if icp.get_param('automotive.label_direct_print_enabled') in {'1', 'true', 'True'} else 'preview_pdf'
        default_copies = max(int(copies or icp.get_param('automotive.label_default_copies') or 1), 1)
        context = {
            'default_source_model': source_record._name if source_record else False,
            'default_source_res_id': source_record.id if source_record else False,
            'default_source_display_name': source_record.display_name if source_record else False,
            'default_label_payload_json': json.dumps(labels, ensure_ascii=False, default=str),
            'default_label_count': max(int(label_count or 1), 1),
            'default_copies': default_copies,
            'default_output_mode': default_output_mode,
            'default_job_name': job_name or (source_record.display_name if source_record else _('Automotive labels')),
            'default_printer_name': printer_name or icp.get_param('automotive.label_printer_name') or False,
            'default_print_command': print_command or icp.get_param('automotive.label_print_command') or False,
        }
        action = self.env.ref('automotive_parts.action_automotive_label_print_wizard').sudo().read()[0]
        action['context'] = context
        return action

    def action_preview_pdf(self):
        self.ensure_one()
        report = self.env.ref('automotive_parts.action_report_automotive_label').with_context(
            automotive_label_print_preview_only=True,
            automotive_label_source_model=self.source_model,
            automotive_label_source_res_id=self.source_res_id,
            automotive_label_print_copies=max(int(self.copies or 1), 1),
            automotive_label_print_job_name=self.job_name or self.source_display_name or _('Automotive labels'),
            automotive_label_printer_name=self.printer_name,
            automotive_label_print_command=self.print_command,
        )
        return report.report_action(
            None,
            data={'labels': self._get_base_labels() * max(int(self.label_count or 1), 1)},
            config=False,
        )

    def action_process(self):
        self.ensure_one()
        if self.output_mode == 'preview_pdf':
            return self.action_preview_pdf()

        report = self.env.ref('automotive_parts.action_report_automotive_label').with_context(
            automotive_label_queue_print=True,
            automotive_label_source_model=self.source_model,
            automotive_label_source_res_id=self.source_res_id,
            automotive_label_print_copies=max(int(self.copies or 1), 1),
            automotive_label_print_job_name=self.job_name or self.source_display_name or _('Automotive labels'),
            automotive_label_printer_name=self.printer_name,
            automotive_label_print_command=self.print_command,
        )
        return report.report_action(
            None,
            data={'labels': self._get_base_labels() * max(int(self.label_count or 1), 1)},
            config=False,
        )
