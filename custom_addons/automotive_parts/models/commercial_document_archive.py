# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError


class CommercialDocumentArchive(models.Model):
    _name = 'commercial.document.archive'
    _description = 'Commercial Document Archive'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'archived_at desc, id desc'
    _check_company_auto = True
    _AUDIT_FIELDS = {
        'name',
        'state',
        'document_type',
        'company_id',
        'partner_id',
        'sale_order_id',
        'picking_id',
        'account_move_id',
        'attachment_id',
        'archived_at',
        'archived_by',
        'note',
    }

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

    def _audit_snapshot(self, field_names=None):
        self.ensure_one()
        tracked_fields = field_names or self._AUDIT_FIELDS
        snapshot = {}
        for field_name in tracked_fields:
            if field_name not in self._fields:
                continue
            value = self[field_name]
            if isinstance(value, models.BaseModel):
                snapshot[field_name] = value.ids
            else:
                snapshot[field_name] = value
        snapshot.update({
            'source_model': self.source_model,
            'source_res_id': self.source_res_id,
            'source_reference': self.source_reference,
            'attachment_name': self.attachment_name,
        })
        return snapshot

    def _audit_log(self, action, description, old_values=None, new_values=None):
        self.ensure_one()
        if self.env.context.get('skip_audit_log') is True:
            return False
        return self.env['automotive.audit.log'].log_change(
            action=action,
            record=self,
            description=description,
            old_values=old_values,
            new_values=new_values,
        )

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

    @api.model
    def _get_source_document_type(self, source):
        if not source:
            return 'other'
        if source._name == 'stock.picking':
            if source.picking_type_code == 'incoming':
                return 'nir'
            if source.picking_type_code == 'outgoing':
                return 'delivery_note'
            return 'internal'
        if source._name == 'account.move':
            return 'vendor_bill' if source.move_type in {'in_invoice', 'in_refund'} else 'customer_invoice'
        if source._name == 'sale.order':
            return 'internal'
        return 'other'

    @api.model
    def _get_source_attachment(self, source):
        if not source:
            return self.env['ir.attachment']

        main_attachment = getattr(source, 'message_main_attachment_id', False)
        if main_attachment:
            return main_attachment

        attachments = self.env['ir.attachment'].search([
            ('res_model', '=', source._name),
            ('res_id', '=', source.id),
        ], order='id desc')
        if not attachments:
            return self.env['ir.attachment']

        pdf_attachments = attachments.filtered(lambda attachment: 'pdf' in (attachment.mimetype or '').lower())
        return pdf_attachments[:1] or attachments[:1]

    @api.model
    def _find_existing_archive(self, source, attachment=False):
        if not source:
            return self.browse()

        company_id = source.company_id.id if source.company_id else self.env.company.id
        source_field_map = {
            'sale.order': 'sale_order_id',
            'stock.picking': 'picking_id',
            'account.move': 'account_move_id',
        }
        source_field = source_field_map.get(source._name)

        if source_field:
            archive = self.search([
                ('company_id', '=', company_id),
                (source_field, '=', source.id),
            ], limit=1)
            if archive:
                return archive

        if attachment:
            return self.search([
                ('company_id', '=', company_id),
                ('attachment_id', '=', attachment.id),
            ], limit=1)

        return self.browse()

    @api.model
    def _prepare_sync_values(self, source, attachment=False, document_type=False, note=False):
        values = {
            'company_id': source.company_id.id if source.company_id else self.env.company.id,
            'document_type': document_type or self._get_source_document_type(source),
        }
        if source._name == 'sale.order':
            values['sale_order_id'] = source.id
        elif source._name == 'stock.picking':
            values['picking_id'] = source.id
        elif source._name == 'account.move':
            values['account_move_id'] = source.id
        if source.partner_id:
            values['partner_id'] = source.partner_id.id
        if attachment:
            values['attachment_id'] = attachment.id
        if note:
            values['note'] = note
        return values

    @api.model
    def sync_from_source_document(self, source, attachment=False, document_type=False, note=False, archive=False):
        """Create or refresh an archive entry from a commercial source document."""
        source = source.exists() if source else self.browse()
        if not source:
            return self.browse()

        attachment = attachment or self._get_source_attachment(source)
        archive_entry = self._find_existing_archive(source, attachment=attachment)
        values = self._prepare_sync_values(
            source,
            attachment=attachment,
            document_type=document_type,
            note=note,
        )

        if archive_entry:
            write_vals = {}
            if values.get('company_id') and archive_entry.company_id.id != values['company_id']:
                write_vals['company_id'] = values['company_id']
            for source_field in ('sale_order_id', 'picking_id', 'account_move_id'):
                if values.get(source_field) and not archive_entry[source_field]:
                    write_vals[source_field] = values[source_field]
            if values.get('partner_id') and not archive_entry.partner_id:
                write_vals['partner_id'] = values['partner_id']
            if values.get('attachment_id') and not archive_entry.attachment_id:
                write_vals['attachment_id'] = values['attachment_id']
            if values.get('document_type') and (not archive_entry.document_type or archive_entry.document_type == 'other'):
                write_vals['document_type'] = values['document_type']
            if values.get('note') and not archive_entry.note:
                write_vals['note'] = values['note']
            if write_vals:
                archive_entry.write(write_vals)
            if archive and archive_entry.attachment_id and archive_entry.state != 'archived':
                archive_entry.action_archive_document()
            return archive_entry

        if not attachment:
            return self.browse()

        archive_entry = self.create(values)
        if archive:
            archive_entry.action_archive_document()
        return archive_entry

    def _resolve_source_document(self):
        self.ensure_one()
        if self.source_model and self.source_res_id:
            return self.env[self.source_model].browse(self.source_res_id).exists()
        return self.browse()

    @api.depends(
        'sale_order_id',
        'sale_order_id.name',
        'picking_id',
        'picking_id.name',
        'account_move_id',
        'account_move_id.name',
        'account_move_id.ref',
    )
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
            record.company_id = move.company_id
            record.partner_id = move.partner_id
            if not record.document_type or record.document_type == 'other':
                record.document_type = 'vendor_bill' if move.move_type in {'in_invoice', 'in_refund'} else 'customer_invoice'

    @api.onchange('picking_id')
    def _onchange_picking_id_sync_metadata(self):
        for record in self.filtered('picking_id'):
            picking = record.picking_id
            record.company_id = picking.company_id
            record.partner_id = picking.partner_id
            if not record.document_type or record.document_type == 'other':
                record.document_type = 'nir' if picking.picking_type_code == 'incoming' else 'delivery_note'

    @api.onchange('sale_order_id')
    def _onchange_sale_order_id_sync_metadata(self):
        for record in self.filtered('sale_order_id'):
            record.company_id = record.sale_order_id.company_id
            if not record.partner_id:
                record.partner_id = record.sale_order_id.partner_id

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if self.env.context.get('skip_audit_log') is not True:
            for record, vals in zip(records, vals_list):
                tracked_fields = [field_name for field_name in vals.keys() if field_name in record._AUDIT_FIELDS] or None
                record._audit_log(
                    action='create',
                    description=f'Commercial document archive created: {record.name}',
                    new_values=record._audit_snapshot(tracked_fields),
                )
        return records

    def write(self, vals):
        context = dict(self.env.context or {})
        tracked_fields = [field_name for field_name in vals.keys() if field_name in self._AUDIT_FIELDS]
        old_by_id = {}
        if tracked_fields and context.get('skip_audit_log') is not True:
            old_by_id = {record.id: record._audit_snapshot(tracked_fields) for record in self}

        result = super().write(vals)

        if tracked_fields and context.get('skip_audit_log') is not True:
            for record in self:
                record._audit_log(
                    action='write',
                    description=f'Commercial document archive updated: {record.name}',
                    old_values=old_by_id.get(record.id),
                    new_values=record._audit_snapshot(tracked_fields),
                )
        return result

    def unlink(self):
        context = dict(self.env.context or {})
        snapshots = {record.id: record._audit_snapshot() for record in self}
        if context.get('skip_audit_log') is not True:
            for record in self:
                record._audit_log(
                    action='unlink',
                    description=f'Commercial document archive deleted: {record.name}',
                    old_values=snapshots.get(record.id),
                )
        return super().unlink()

    def action_archive_document(self):
        for record in self:
            source = record._resolve_source_document()
            if source:
                record.sync_from_source_document(
                    source,
                    attachment=record.attachment_id,
                    document_type=record.document_type,
                    note=record.note,
                    archive=False,
                )

            if not record.attachment_id:
                raise UserError('Select an attachment or sync from source before archiving the document.')
            old_values = record._audit_snapshot()
            record.with_context(skip_audit_log=True).write({
                'state': 'archived',
                'archived_at': fields.Datetime.now(),
                'archived_by': self.env.user.id,
            })
            record._audit_log(
                action='custom',
                description=f'Commercial document archived: {record.name}',
                old_values=old_values,
                new_values=record._audit_snapshot(),
            )
        return True

    def action_sync_from_source_document(self):
        for record in self:
            source = record._resolve_source_document()
            if not source:
                raise UserError('This archive entry is not linked to a source document.')
            record.sync_from_source_document(
                source,
                attachment=record.attachment_id,
                document_type=record.document_type,
                note=record.note,
                archive=False,
            )
        return True

    def action_reset_to_draft(self):
        for record in self:
            old_values = record._audit_snapshot()
            record.with_context(skip_audit_log=True).write({'state': 'draft'})
            record._audit_log(
                action='custom',
                description=f'Commercial document archive reset to draft: {record.name}',
                old_values=old_values,
                new_values=record._audit_snapshot(),
            )
        return True

    def action_cancel_archive(self):
        for record in self:
            old_values = record._audit_snapshot()
            record.with_context(skip_audit_log=True).write({'state': 'cancelled'})
            record._audit_log(
                action='custom',
                description=f'Commercial document archive cancelled: {record.name}',
                old_values=old_values,
                new_values=record._audit_snapshot(),
            )
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
