# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError


class CommercialDocumentArchive(models.Model):
    _name = 'commercial.document.archive'
    _description = 'Commercial Document Archive'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'archived_at desc, id desc'
    _check_company_auto = True

    _sql_constraints = [
        (
            'commercial_document_archive_attachment_unique',
            'unique(attachment_id)',
            'This attachment is already archived as a commercial document.',
        ),
    ]

    name = fields.Char(
        string='Archive ID',
        required=True,
        copy=False,
        default=lambda self: self.env['ir.sequence'].next_by_code('commercial.document.archive') or 'DOC/NEW',
        readonly=True,
        tracking=True,
    )
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('archived', 'Archived'),
            ('cancelled', 'Cancelled'),
        ],
        default='draft',
        required=True,
        tracking=True,
    )
    document_type = fields.Selection(
        [
            ('nir', 'NIR'),
            ('vendor_bill', 'Vendor Bill'),
            ('customer_invoice', 'Customer Invoice'),
            ('delivery_note', 'Delivery Note'),
            ('receipt', 'Receipt'),
            ('internal', 'Internal Document'),
            ('other', 'Other'),
        ],
        string='Document Type',
        required=True,
        default='other',
        tracking=True,
    )
    company_id = fields.Many2one(
        'res.company',
        required=True,
        index=True,
        default=lambda self: self.env.company,
        tracking=True,
    )
    partner_id = fields.Many2one('res.partner', string='Partner', index=True, tracking=True, check_company=True)
    sale_order_id = fields.Many2one('sale.order', string='Order', index=True, tracking=True, check_company=True)
    picking_id = fields.Many2one('stock.picking', string='Picking', index=True, tracking=True, check_company=True)
    account_move_id = fields.Many2one('account.move', string='Invoice / Bill', index=True, tracking=True, check_company=True)
    attachment_id = fields.Many2one(
        'ir.attachment',
        string='Attachment',
        required=True,
        ondelete='restrict',
        tracking=True,
    )
    attachment_name = fields.Char(string='File Name', related='attachment_id.name', store=True, readonly=True)
    attachment_mimetype = fields.Char(string='MIME Type', related='attachment_id.mimetype', store=True, readonly=True)
    attachment_file_size = fields.Integer(string='File Size', related='attachment_id.file_size', store=True, readonly=True)
    archived_at = fields.Datetime(string='Archived At', readonly=True, tracking=True)
    archived_by = fields.Many2one('res.users', string='Archived By', readonly=True, tracking=True)
    source_reference = fields.Char(string='Source Reference', compute='_compute_source_reference', store=True)
    source_model = fields.Char(string='Source Model', compute='_compute_source_record', store=True)
    source_res_id = fields.Integer(string='Source Record ID', compute='_compute_source_record', store=True)
    note = fields.Text(string='Notes')

    @api.constrains('company_id', 'partner_id', 'sale_order_id', 'picking_id', 'account_move_id', 'attachment_id')
    def _check_source_document_consistency(self):
        for record in self:
            sources = [record.sale_order_id, record.picking_id, record.account_move_id]
            source_partners = {
                source.partner_id.commercial_partner_id.id
                for source in sources
                if source and source.partner_id
            }
            if len(source_partners) > 1:
                raise UserError('Linked commercial source documents must belong to the same commercial partner.')

            source_companies = {
                source.company_id.id
                for source in sources
                if source and source.company_id
            }
            if source_companies and source_companies != {record.company_id.id}:
                raise UserError('All linked commercial source documents must belong to the same company as the archive entry.')

            if record.partner_id and source_partners and record.partner_id.commercial_partner_id.id not in source_partners:
                raise UserError('The selected partner must match the linked commercial source documents.')

            if (
                record.attachment_id
                and record.attachment_id.company_id
                and record.attachment_id.company_id != record.company_id
            ):
                raise UserError('The archived attachment must belong to the same company as the archive entry.')

    @api.depends('sale_order_id', 'picking_id', 'account_move_id')
    def _compute_source_reference(self):
        for record in self:
            record.source_reference = (
                record.account_move_id.name
                or record.account_move_id.ref
                or record.picking_id.name
                or record.sale_order_id.name
                or False
            )

    @api.depends('sale_order_id', 'picking_id', 'account_move_id')
    def _compute_source_record(self):
        for record in self:
            source_model = False
            source_res_id = False
            if record.account_move_id:
                source_model = 'account.move'
                source_res_id = record.account_move_id.id
            elif record.picking_id:
                source_model = 'stock.picking'
                source_res_id = record.picking_id.id
            elif record.sale_order_id:
                source_model = 'sale.order'
                source_res_id = record.sale_order_id.id
            record.source_model = source_model
            record.source_res_id = source_res_id

    @api.onchange('account_move_id')
    def _onchange_account_move_id_sync_metadata(self):
        for record in self.filtered('account_move_id'):
            move = record.account_move_id
            record.partner_id = move.partner_id
            if not record.document_type or record.document_type == 'other':
                record.document_type = 'vendor_bill' if move.move_type in {'in_invoice', 'in_refund'} else 'customer_invoice'

    @api.onchange('picking_id')
    def _onchange_picking_id_sync_metadata(self):
        for record in self.filtered('picking_id'):
            picking = record.picking_id
            record.partner_id = picking.partner_id
            if not record.document_type or record.document_type == 'other':
                record.document_type = 'nir' if picking.picking_type_code == 'incoming' else 'delivery_note'

    @api.onchange('sale_order_id')
    def _onchange_sale_order_id_sync_metadata(self):
        for record in self.filtered('sale_order_id'):
            if not record.partner_id:
                record.partner_id = record.sale_order_id.partner_id

    def action_archive_document(self):
        for record in self:
            if not record.attachment_id:
                raise UserError('Select an attachment before archiving the document.')
            record.write({
                'state': 'archived',
                'archived_at': fields.Datetime.now(),
                'archived_by': self.env.user.id,
            })
        return True

    def action_reset_to_draft(self):
        self.write({'state': 'draft'})
        return True

    def action_cancel_archive(self):
        self.write({'state': 'cancelled'})
        return True

    def action_open_attachment(self):
        self.ensure_one()
        if not self.attachment_id:
            raise UserError('No attachment is linked to this archive entry.')
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{self.attachment_id.id}?download=false',
            'target': 'new',
        }

    def action_open_source_document(self):
        self.ensure_one()
        if not self.source_model or not self.source_res_id:
            raise UserError('This archive entry is not linked to a source document.')
        return {
            'type': 'ir.actions.act_window',
            'res_model': self.source_model,
            'res_id': self.source_res_id,
            'view_mode': 'form',
            'target': 'current',
        }
