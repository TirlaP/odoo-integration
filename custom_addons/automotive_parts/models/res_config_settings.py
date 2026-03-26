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
