# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    automotive_ready_email_enabled = fields.Boolean(
        string='Send ready email notifications',
        default=False,
    )
    automotive_ready_email_template_id = fields.Many2one(
        'mail.template',
        string='Ready email template',
        domain="[('model', '=', 'sale.order')]",
    )


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    automotive_label_direct_print_enabled = fields.Boolean(
        string='Enable direct label printing',
        config_parameter='automotive.label_direct_print_enabled',
    )
    automotive_label_printer_name = fields.Char(
        string='Label printer name',
        config_parameter='automotive.label_printer_name',
    )
    automotive_label_print_command = fields.Char(
        string='Label print command',
        config_parameter='automotive.label_print_command',
        help='Optional override for the server-side print command. Leave empty to auto-detect lp/lpr.',
    )
    automotive_ready_email_enabled = fields.Boolean(
        related='company_id.automotive_ready_email_enabled',
        readonly=False,
        string='Send ready email notifications',
    )
    automotive_ready_email_template_id = fields.Many2one(
        related='company_id.automotive_ready_email_template_id',
        readonly=False,
        string='Ready email template',
        domain="[('model', '=', 'sale.order')]",
    )
