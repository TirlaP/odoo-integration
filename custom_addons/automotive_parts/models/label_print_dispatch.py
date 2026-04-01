# -*- coding: utf-8 -*-
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from odoo import _, models
from odoo.exceptions import UserError


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
                'label_count': len(labels) or len(docids or []),
                'dispatch_mode': dispatch_mode,
                'direct_print_enabled': settings['enabled'],
                'printer_name': settings['printer_name'],
                'job_name': settings['job_name'],
                'copies': settings['copies'],
            },
        )

    def _get_label_print_settings(self):
        icp = self.env['ir.config_parameter'].sudo()
        return {
            'enabled': icp.get_param('automotive.label_direct_print_enabled') in {'1', 'true', 'True'},
            'printer_name': (icp.get_param('automotive.label_printer_name') or '').strip(),
            'command': (icp.get_param('automotive.label_print_command') or '').strip(),
            'copies': max(int(self.env.context.get('automotive_label_print_copies') or 1), 1),
            'job_name': (
                self.env.context.get('automotive_label_print_job_name')
                or _('Automotive labels')
            ),
        }

    def _get_label_print_command(self, settings):
        command = settings['command']
        if command:
            command_path = Path(command)
            command_name = command_path.name
            if command_name not in {'lp', 'lpr'}:
                raise UserError(_('Only lp or lpr may be configured for server-side label printing.'))
            if command_path.is_absolute():
                if not command_path.exists():
                    raise UserError(_('The configured label print command does not exist on the server.'))
                return str(command_path)
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

    def report_action(self, docids, data=None, config=True):
        self.ensure_one()
        if not self._is_automotive_label_report():
            return super().report_action(docids, data=data, config=config)

        settings = self._get_label_print_settings()
        if not settings['enabled']:
            self._audit_label_request(docids, data, settings, 'requested (pdf)')
            return super().report_action(docids, data=data, config=config)

        pdf_content = self._render_label_report_pdf(data=data)
        result = self._dispatch_label_pdf_to_printer(pdf_content, settings)
        self._audit_label_request(docids, data, settings, 'dispatched to printer')
        return result
