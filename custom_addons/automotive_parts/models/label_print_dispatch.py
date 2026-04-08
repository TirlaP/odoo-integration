# -*- coding: utf-8 -*-
import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from odoo import _, models
from odoo.exceptions import UserError
from odoo.tools.pdf import PdfFileReader, PdfFileWriter


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    _LABEL_REPORT_XMLID = 'automotive_parts.action_report_automotive_label'
    _LABEL_REPORT_NAME = 'automotive_parts.report_product_label'

    def _is_automotive_label_report(self):
        self.ensure_one()
        return self.report_name == self._LABEL_REPORT_NAME

    def _audit_label_request(self, docids, data, settings, dispatch_mode):
        self.ensure_one()
        if self.env.context.get('skip_audit_log') is True:
            return False
        labels = list((data or {}).get('labels') or [])
        return self.env['automotive.audit.log'].log_change(
            action='custom',
            record=self,
            description=f'Automotive label report {dispatch_mode}',
            new_values={
                'report_name': self.report_name,
                'report_xmlid': self._LABEL_REPORT_XMLID,
                'active_model': self.env.context.get('active_model') or False,
                'docids': docids or [],
                'label_count': self._count_label_pages(labels, repeat_count=self._get_label_repeat_count(data))
                or len(docids or []),
                'dispatch_mode': dispatch_mode,
                'direct_print_enabled': settings['enabled'],
                'queue_requested': settings['queue_requested'],
                'printer_name': settings['printer_name'],
                'job_name': settings['job_name'],
                'copies': settings['copies'],
                'repeat_count': self._get_label_repeat_count(data),
            },
        )

    def _get_label_print_settings(self):
        icp = self.env['ir.config_parameter'].sudo()
        preview_only = self.env.context.get('automotive_label_print_preview_only') is True
        return {
            'enabled': (not preview_only) and icp.get_param('automotive.label_direct_print_enabled') in {'1', 'true', 'True'},
            'queue_requested': self.env.context.get('automotive_label_queue_print') is True,
            'printer_name': (self.env.context.get('automotive_label_printer_name') or icp.get_param('automotive.label_printer_name') or '').strip(),
            'command': (self.env.context.get('automotive_label_print_command') or icp.get_param('automotive.label_print_command') or '').strip(),
            'copies': max(
                int(
                    self.env.context.get('automotive_label_print_copies')
                    or icp.get_param('automotive.label_default_copies')
                    or 1
                ),
                1,
            ),
            'job_name': (
                self.env.context.get('automotive_label_print_job_name')
                or _('Automotive labels')
            ),
            'source_model': self.env.context.get('automotive_label_source_model') or self.env.context.get('active_model') or False,
            'source_res_id': self.env.context.get('automotive_label_source_res_id') or self.env.context.get('active_id') or False,
        }

    def _get_label_payloads(self, docids, data=None):
        self.ensure_one()
        labels = list((data or {}).get('labels') or [])
        if labels:
            return labels
        if not docids:
            return []
        report_model = self.env['report.automotive_parts.report_product_label']
        try:
            report_values = report_model._get_report_values(docids, data=data)
        except Exception:
            return []
        return list(report_values.get('docs') or [])

    @staticmethod
    def _get_label_repeat_count(data=None):
        try:
            return max(int((data or {}).get('repeat_count') or 1), 1)
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _count_label_pages(labels, repeat_count=1):
        page_count = 0
        for label in labels or []:
            try:
                qty = int(label.get('qty') or 1)
            except (TypeError, ValueError, AttributeError):
                qty = 1
            page_count += max(qty, 1)
        return page_count * max(int(repeat_count or 1), 1)

    def _duplicate_pdf_pages(self, pdf_content, repeat_count):
        self.ensure_one()
        repeat_count = max(int(repeat_count or 1), 1)
        if repeat_count <= 1 or not pdf_content:
            return pdf_content

        reader = PdfFileReader(io.BytesIO(pdf_content), strict=False)
        if reader.getNumPages() <= 0:
            return pdf_content

        writer = PdfFileWriter()
        for _idx in range(repeat_count):
            for page_index in range(reader.getNumPages()):
                writer.addPage(reader.getPage(page_index))

        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()

    def _get_label_print_command(self, settings):
        command = settings['command']
        if command:
            command_path = Path(command)
            command_name = command_path.name
            if command_name not in {'lp', 'lpr'}:
                raise UserError(_('Only lp or lpr may be configured for server-side label printing.'))
            if command_path.is_absolute():
                raise UserError(
                    _('Absolute command paths are not allowed for label printing. Configure only lp or lpr.')
                )
            resolved = shutil.which(command_name)
            if not resolved:
                raise UserError(_('The configured label print command is not available on the server PATH.'))
            return resolved
        if shutil.which('lp'):
            return 'lp'
        if shutil.which('lpr'):
            return 'lpr'
        return ''

    def _build_label_print_args(self, command, pdf_path, settings):
        copies = settings['copies']
        printer_name = settings['printer_name']
        job_name = settings['job_name']
        if command.endswith('lp'):
            args = [command]
            if printer_name:
                args.extend(['-d', printer_name])
            if job_name:
                args.extend(['-t', job_name])
            if copies > 1:
                args.extend(['-n', str(copies)])
            args.append(pdf_path)
            return args

        args = [command]
        if printer_name:
            args.extend(['-P', printer_name])
        if job_name:
            args.extend(['-J', job_name])
        if copies > 1:
            args.extend(['-#', str(copies)])
        args.append(pdf_path)
        return args

    def _dispatch_label_pdf_to_printer(self, pdf_content, settings):
        command = self._get_label_print_command(settings)
        if not command:
            raise UserError(
                _('No server-side print command is available. Install lp/lpr or configure automotive.label_print_command.')
            )
        if not settings['printer_name']:
            raise UserError(
                _('No label printer is configured. Set automotive.label_printer_name or pass automotive_label_printer_name in context.')
            )

        with tempfile.NamedTemporaryFile(prefix='automotive_label_', suffix='.pdf', delete=False) as tmp:
            tmp.write(pdf_content)
            tmp.flush()
            tmp_path = tmp.name

        try:
            args = self._build_label_print_args(command, tmp_path, settings)
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        if result.returncode != 0:
            error_output = (result.stderr or result.stdout or '').strip()
            raise UserError(
                _('Label print dispatch failed for printer "%(printer)s": %(error)s') % {
                    'printer': settings['printer_name'],
                    'error': error_output or _('unknown print error'),
                }
            )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Eticheta a fost trimisă la imprimantă'),
                'message': _(
                    'Jobul "%(job)s" a fost trimis la imprimanta "%(printer)s".'
                ) % {
                    'job': settings['job_name'],
                    'printer': settings['printer_name'],
                },
                'type': 'success',
                'sticky': False,
            },
        }

    def _enqueue_label_print_job(self, docids, data, settings):
        self.ensure_one()
        labels = self._get_label_payloads(docids, data=data)
        if not labels:
            raise UserError(_('No labels were prepared for queued printing.'))

        source_record = False
        try:
            if settings['source_model'] and settings['source_res_id']:
                source_record = self.env[settings['source_model']].browse(settings['source_res_id']).exists()
            elif self.env.context.get('active_model') and docids:
                source_record = self.env[self.env.context['active_model']].browse(docids[0]).exists()
        except Exception:
            source_record = False

        payload = {
            'labels': labels,
            'repeat_count': self._get_label_repeat_count(data),
            'printer_name': settings['printer_name'],
            'print_command': settings['command'],
            'copies': settings['copies'],
            'job_name': settings['job_name'],
            'report_name': self.report_name,
        }
        batch_name = settings['job_name'] or _('Automotive labels')
        return self.env['automotive.async.job'].enqueue_call(
            'ir.actions.report',
            '_run_automotive_async_label_job',
            target_res_id=self.id,
            name=batch_name,
            args=[],
            kwargs={'payload': payload},
            payload=payload,
            source_record=source_record or False,
            batch_name=batch_name,
            priority=20,
            requested_by_id=self.env.user.id,
            run_as_user_id=self.env.user.id,
            company_id=self.env.company.id,
            max_attempts=3,
        )

    def _run_automotive_async_label_job(self, payload=None):
        self.ensure_one()
        payload = payload or {}
        labels = [label for label in (payload.get('labels') or []) if label and label.get('barcode')]
        if not labels:
            raise UserError(_('No labels were prepared for printing.'))

        async_context = {
            'automotive_label_print_preview_only': False,
            'automotive_label_queue_print': False,
            'automotive_label_printer_name': payload.get('printer_name') or '',
            'automotive_label_print_command': payload.get('print_command') or '',
            'automotive_label_print_copies': max(int(payload.get('copies') or 1), 1),
            'automotive_label_print_job_name': payload.get('job_name') or _('Automotive labels'),
        }
        report = self.with_context(**async_context)
        settings = report._get_label_print_settings()
        pdf_content = report._render_label_report_pdf(
            data={
                'labels': labels,
                'repeat_count': self._get_label_repeat_count(payload),
            }
        )
        return report._dispatch_label_pdf_to_printer(pdf_content, settings)

    def _render_label_report_pdf(self, data=None):
        self.ensure_one()
        pdf_content, _content_type = self.with_context(force_report_rendering=True)._render_qweb_pdf(
            self.report_name,
            [],
            data=data,
        )
        if not pdf_content:
            raise UserError(_('The label PDF could not be rendered for direct printing.'))
        return pdf_content

    def _render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        report = self if len(self) == 1 else self._get_report(report_ref)
        if (
            report.report_name != self._LABEL_REPORT_NAME
            or self.env.context.get('automotive_label_skip_pdf_duplication')
        ):
            return super()._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)

        repeat_count = report._get_label_repeat_count(data)
        base_data = dict(data or {})
        base_data['repeat_count'] = 1
        pdf_content, report_type = report.with_context(
            automotive_label_skip_pdf_duplication=True
        )._render_qweb_pdf(report_ref, res_ids=res_ids, data=base_data)
        if report_type != 'pdf' or repeat_count <= 1:
            return pdf_content, report_type
        return report._duplicate_pdf_pages(pdf_content, repeat_count), report_type

    def report_action(self, docids, data=None, config=True):
        self.ensure_one()
        if not self._is_automotive_label_report():
            return super().report_action(docids, data=data, config=config)

        settings = self._get_label_print_settings()
        labels = self._get_label_payloads(docids, data=data)
        if not labels:
            raise UserError(_('No labels were prepared for printing.'))
        effective_data = dict(data or {})
        effective_data['labels'] = labels

        if (settings['enabled'] or settings['queue_requested']) and not settings['printer_name']:
            raise UserError(
                _('No label printer is configured. Set automotive.label_printer_name or disable direct printing.')
            )

        if (settings['enabled'] or settings['queue_requested']) and not self.env.context.get('skip_automotive_async_queue'):
            job = self._enqueue_label_print_job(docids, effective_data, settings)
            self._audit_label_request(docids, effective_data, settings, f'queued as {job.display_name}')
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Eticheta a fost pusă în coadă'),
                    'message': _(
                        'Jobul "%(job)s" a fost pus în coadă pentru imprimanta "%(printer)s".'
                    ) % {
                        'job': job.display_name,
                        'printer': settings['printer_name'],
                    },
                    'type': 'info',
                    'sticky': False,
                },
            }

        if self.env.context.get('automotive_label_print_preview_only') is True or not settings['enabled']:
            self._audit_label_request(docids, effective_data, settings, 'requested (pdf)')
            return super().report_action(docids, data=data, config=config)

        pdf_content = self._render_label_report_pdf(data=effective_data)
        result = self._dispatch_label_pdf_to_printer(pdf_content, settings)
        self._audit_label_request(docids, effective_data, settings, 'dispatched to printer')
        return result
