# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.http import request as http_request


class MechanicPortalRequest(models.Model):
    _name = 'mechanic.portal.request'
    _description = 'Mechanic Portal Request'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'portal.mixin']
    _order = 'create_date desc, id desc'
    _AUDIT_FIELDS = {
        'name',
        'partner_id',
        'request_user_id',
        'company_id',
        'sale_order_id',
        'request_type',
        'description',
        'state',
        'resolved_on',
    }

    name = fields.Char(
        string='Request Number',
        required=True,
        copy=False,
        default='/',
        readonly=True,
        tracking=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Mechanic',
        required=True,
        index=True,
        tracking=True,
    )
    request_user_id = fields.Many2one(
        'res.users',
        string='Requested By',
        required=True,
        default=lambda self: self.env.user,
        tracking=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    sale_order_id = fields.Many2one(
        'sale.order',
        string='Related Order',
        tracking=True,
    )
    request_type = fields.Selection(
        [
            ('order', 'Order Clarification'),
            ('document', 'Document Request'),
            ('payment', 'Payment / Balance'),
            ('general', 'General Request'),
        ],
        string='Request Type',
        required=True,
        default='general',
        tracking=True,
    )
    description = fields.Text(string='Description', required=True)
    state = fields.Selection(
        [
            ('new', 'Nouă'),
            ('in_progress', 'În lucru'),
            ('waiting_customer', 'Așteaptă răspuns client'),
            ('done', 'Rezolvată'),
            ('cancelled', 'Anulată'),
        ],
        string='Status',
        default='new',
        required=True,
        tracking=True,
    )
    resolved_on = fields.Datetime(string='Resolved On', tracking=True)

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
        return snapshot

    def _audit_origin(self):
        try:
            http_request_path = getattr(getattr(http_request, 'httprequest', None), 'path', '')
        except RuntimeError:
            http_request_path = ''
        if http_request_path.startswith('/my/mechanic'):
            return 'portal'
        return 'backend'

    def _audit_context_summary(self):
        self.ensure_one()
        return {
            'origin': self._audit_origin(),
            'request_type': self.request_type,
            'state': self.state,
            'sale_order_id': self.sale_order_id.id if self.sale_order_id else False,
            'sale_order_name': self.sale_order_id.name if self.sale_order_id else False,
        }

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

    def _compute_access_url(self):
        super()._compute_access_url()
        for request_record in self:
            request_record.access_url = '/my/mechanic/requests/%s' % request_record.id

    @api.constrains('sale_order_id', 'partner_id')
    def _check_sale_order_mechanic_scope(self):
        for request_record in self:
            if not request_record.sale_order_id:
                continue
            if not request_record.sale_order_id.mechanic_partner_id:
                raise ValidationError(_('The related order is not assigned to a mechanic portal account.'))
            sale_mechanic = request_record.sale_order_id.mechanic_partner_id.commercial_partner_id
            if sale_mechanic and sale_mechanic != request_record.partner_id.commercial_partner_id:
                raise ValidationError(_('The related order does not belong to this mechanic portal account.'))

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env['ir.sequence']
        for vals in vals_list:
            if vals.get('name', '/') == '/':
                vals['name'] = sequence.next_by_code('mechanic.portal.request') or '/'
            if not vals.get('company_id'):
                vals['company_id'] = self.env.company.id
            if not vals.get('request_user_id'):
                vals['request_user_id'] = self.env.user.id

        requests = super().create(vals_list)
        for request_record, vals in zip(requests, vals_list):
            tracked_fields = [field_name for field_name in vals.keys() if field_name in request_record._AUDIT_FIELDS]
            request_record._audit_log(
                action='create',
                description=f'Mechanic portal request created from {request_record._audit_origin()}: {request_record.name}',
                new_values={**request_record._audit_context_summary(), **request_record._audit_snapshot(tracked_fields)},
            )
        return requests

    def write(self, vals):
        old_values = {}
        state_before = {}
        if vals.get('state') == 'done' and not vals.get('resolved_on'):
            vals['resolved_on'] = fields.Datetime.now()
        elif vals.get('state') != 'done' and 'state' in vals and 'resolved_on' not in vals:
            vals['resolved_on'] = False
        tracked_fields = [field_name for field_name in vals.keys() if field_name in self._AUDIT_FIELDS]
        if tracked_fields and self.env.context.get('skip_audit_log') is not True:
            old_values = {
                request_record.id: request_record._audit_snapshot(tracked_fields)
                for request_record in self
            }
        if 'state' in vals and self.env.context.get('skip_audit_log') is not True:
            state_before = {request_record.id: request_record.state for request_record in self}

        result = super().write(vals)

        if tracked_fields and self.env.context.get('skip_audit_log') is not True:
            for request_record in self:
                request_record._audit_log(
                    action='write',
                    description=f'Mechanic portal request updated: {request_record.name}',
                    old_values=old_values.get(request_record.id),
                    new_values=request_record._audit_snapshot(tracked_fields),
                )

        if 'state' in vals and self.env.context.get('skip_audit_log') is not True:
            for request_record in self:
                old_state = state_before.get(request_record.id)
                if old_state == request_record.state:
                    continue
                request_record._audit_log(
                    action='custom',
                    description=(
                        f'Mechanic portal request lifecycle transition: '
                        f'{request_record.name} {old_state or "unknown"} -> {request_record.state}'
                    ),
                    old_values={
                        'state': old_state,
                        'resolved_on': old_values.get(request_record.id, {}).get('resolved_on'),
                    },
                    new_values={
                        **request_record._audit_context_summary(),
                        'resolved_on': request_record.resolved_on,
                    },
                )
        return result

    def unlink(self):
        for request_record in self:
            request_record._audit_log(
                action='unlink',
                description=f'Mechanic portal request deleted: {request_record.name}',
                old_values=request_record._audit_snapshot(),
            )
        return super().unlink()

    def action_mark_in_progress(self):
        self.write({'state': 'in_progress'})

    def action_mark_waiting_customer(self):
        self.write({'state': 'waiting_customer'})

    def action_mark_done(self):
        self.write({'state': 'done'})

    def action_mark_cancelled(self):
        self.write({'state': 'cancelled'})
