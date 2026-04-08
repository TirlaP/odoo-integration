# -*- coding: utf-8 -*-
from odoo import api, fields, models

from .invoice_ingest_parse_utils import normalize_cui_digits
from .invoice_ingest_shared import (
    _logger,
    normalize_invoice_number,
    normalize_invoice_number_key,
)


class InvoiceIngestJobIdentity(models.Model):
    _inherit = 'invoice.ingest.job'

    @api.model
    def _normalize_invoice_number(self, invoice_number):
        return normalize_invoice_number(invoice_number)

    @api.model
    def _normalize_invoice_number_key(self, invoice_number):
        return normalize_invoice_number_key(invoice_number)

    @api.model
    def _normalize_cui_digits(self, value):
        return normalize_cui_digits(value)

    def _search_supplier_partner_by_vat(self, supplier_vat):
        self.ensure_one()
        clean_vat = self._normalize_cui_digits(supplier_vat)
        if not clean_vat:
            return self.env['res.partner']
        Partner = self.env['res.partner']
        return (
            Partner.search([('vat', '=', clean_vat)], limit=1)
            or Partner.search([('vat', '=ilike', f'RO{clean_vat}')], limit=1)
            or Partner.search([('cui', '=', clean_vat)], limit=1)
            or Partner.search([('cui', '=ilike', f'RO{clean_vat}')], limit=1)
        )

    def _search_supplier_partner_by_code(self, supplier_code):
        self.ensure_one()
        clean_code = (supplier_code or '').strip()
        if not clean_code:
            return self.env['res.partner']
        Partner = self.env['res.partner']
        return (
            Partner.search([('name', '=ilike', clean_code)], limit=1)
            or Partner.search([('ref', '=ilike', clean_code)], limit=1)
        )

    def _search_supplier_partner_by_name(self, supplier_name):
        self.ensure_one()
        clean_name = (supplier_name or '').strip()
        if not clean_name:
            return self.env['res.partner']
        return self.env['res.partner'].search([('name', '=ilike', clean_name)], limit=1)

    def _find_supplier_partner(self, supplier_name=None, supplier_code=None, supplier_vat=None):
        self.ensure_one()
        if self.partner_id:
            return self.partner_id
        return (
            self._search_supplier_partner_by_vat(supplier_vat)
            or self._search_supplier_partner_by_code(supplier_code)
            or self._search_supplier_partner_by_name(supplier_name)
        )

    def _get_or_create_supplier_partner(self, supplier_name=None, supplier_code=None, supplier_vat=None):
        self.ensure_one()
        partner = self._find_supplier_partner(
            supplier_name=supplier_name,
            supplier_code=supplier_code,
            supplier_vat=supplier_vat,
        )
        if partner:
            return partner

        clean_name = (supplier_name or '').strip()
        clean_vat = self._normalize_cui_digits(supplier_vat)
        if not clean_name and not clean_vat:
            return self.env['res.partner']
        if not clean_name:
            clean_name = f'Supplier {clean_vat}'

        vals = {
            'name': clean_name,
            'company_type': 'company',
            'supplier_rank': 1,
        }
        if 'client_type' in self.env['res.partner']._fields:
            vals['client_type'] = 'company'
        if clean_vat:
            vals['vat'] = f'RO{clean_vat}'
            if 'cui' in self.env['res.partner']._fields:
                vals['cui'] = clean_vat

        partner = self.env['res.partner'].sudo().create(vals)
        _logger.info(
            "Auto-created supplier partner id=%s name=%s vat=%s for invoice ingest job id=%s",
            partner.id,
            partner.name,
            vals.get('vat'),
            self.id,
        )
        return partner

    def _resolve_supplier_for_billing(self):
        self.ensure_one()
        if self.partner_id:
            return self.partner_id
        if self.picking_id and self.picking_id.partner_id:
            self.partner_id = self.picking_id.partner_id.id
            return self.partner_id

        payload = self._get_payload_dict()
        normalized = self._get_normalized_invoice_payload()
        supplier = self._get_or_create_supplier_partner(
            supplier_name=(normalized.get('supplier_name') or '').strip(),
            supplier_code=(normalized.get('supplier_code') or '').strip(),
            supplier_vat=(normalized.get('supplier_vat') or '').strip(),
        )
        if supplier:
            self.partner_id = supplier.id
            return self.partner_id

        parsed_payload = payload.get('parsed') or {}
        supplier = self._search_supplier_partner_by_vat(parsed_payload.get('supplier_cui'))
        if supplier:
            self.partner_id = supplier.id
            return self.partner_id
        return self.env['res.partner']

    @api.model
    def _invoice_number_matches_candidate(self, candidate, normalized_invoice, normalized_key):
        candidate_number = candidate.invoice_number or ''
        if normalized_key and self._normalize_invoice_number_key(candidate_number) == normalized_key:
            return True
        return self._normalize_invoice_number(candidate_number).upper() == normalized_invoice.upper()

    @api.model
    def _amount_total_matches_candidate(self, candidate, amount_total):
        if amount_total in (None, False):
            return True
        return abs((candidate.amount_total or 0.0) - amount_total) < 0.01

    @api.model
    def _select_duplicate_candidate(self, candidates, invoice_date=None, amount_total=None):
        if not candidates:
            return self.browse()

        if invoice_date or amount_total not in (None, False):
            exact_matches = candidates.filtered(
                lambda job: (
                    (not invoice_date or job.invoice_date == invoice_date)
                    and self._amount_total_matches_candidate(job, amount_total)
                )
            )
            if exact_matches:
                return exact_matches[:1]

            dated_matches = candidates.filtered(lambda job: invoice_date and job.invoice_date == invoice_date)
            if dated_matches:
                return dated_matches[:1]

            amount_matches = candidates.filtered(lambda job: self._amount_total_matches_candidate(job, amount_total))
            if amount_matches:
                return amount_matches[:1]

            return self.browse()

        return candidates[:1]

    @api.model
    def _find_duplicate_job(
        self,
        source,
        external_id=None,
        partner_id=None,
        invoice_number=None,
        invoice_date=None,
        amount_total=None,
        document_type=None,
    ):
        if external_id:
            existing = self.search([('external_id', '=', external_id)], order='id desc', limit=1)
            if existing:
                return existing

        normalized_invoice = self._normalize_invoice_number(invoice_number)
        normalized_key = self._normalize_invoice_number_key(invoice_number)
        if partner_id and normalized_invoice:
            domain = [
                ('partner_id', '=', partner_id),
                ('invoice_number', '!=', False),
            ]
            if document_type:
                domain.append(('document_type', '=', document_type))
            candidates = self.search(domain, order='id desc')
            candidates = candidates.filtered(
                lambda job: self._invoice_number_matches_candidate(
                    job,
                    normalized_invoice=normalized_invoice,
                    normalized_key=normalized_key,
                )
            )
            selected = self._select_duplicate_candidate(
                candidates,
                invoice_date=invoice_date,
                amount_total=amount_total,
            )
            if selected:
                return selected

        return self.browse()

    @api.model
    def upsert_invoice_job(
        self,
        *,
        source='manual',
        external_id=None,
        partner_id=None,
        invoice_number=None,
        invoice_date=None,
        amount_total=None,
        currency_id=None,
        picking_id=None,
        attachment_id=None,
        document_type=None,
        payload=None,
        batch_uid=None,
        batch_name=None,
        batch_index=None,
        batch_total=None,
    ):
        existing = self._find_duplicate_job(
            source=source,
            external_id=external_id,
            partner_id=partner_id,
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            amount_total=amount_total,
            document_type=document_type,
        )
        if existing:
            vals = {}
            if existing.state == 'failed':
                vals['state'] = 'pending'
            if not existing.partner_id and partner_id:
                vals['partner_id'] = partner_id
            if not existing.invoice_number and invoice_number:
                vals['invoice_number'] = self._normalize_invoice_number(invoice_number)
            if not existing.invoice_date and invoice_date:
                vals['invoice_date'] = invoice_date
            if (not existing.amount_total) and amount_total:
                vals['amount_total'] = amount_total
            if not existing.currency_id and currency_id:
                vals['currency_id'] = currency_id
            if not existing.picking_id and picking_id:
                vals['picking_id'] = picking_id
            if not existing.attachment_id and attachment_id:
                vals['attachment_id'] = attachment_id
            if not existing.document_type and document_type:
                vals['document_type'] = document_type
            if batch_uid:
                vals['batch_uid'] = batch_uid
            if batch_name:
                vals['batch_name'] = batch_name
            if batch_index not in (None, False):
                vals['batch_index'] = batch_index
            if batch_total not in (None, False):
                vals['batch_total'] = batch_total
            if existing.state in {'pending', 'failed'} and not existing.queued_at:
                vals['queued_at'] = fields.Datetime.now()
            if vals:
                existing.write(vals)
            if payload:
                existing._set_payload(payload)
            return existing, False

        vals = {
            'name': f'{(source or "manual").upper()} - {invoice_number or external_id or fields.Datetime.now()}',
            'source': source or 'manual',
            'state': 'pending',
            'external_id': external_id,
            'partner_id': partner_id,
            'invoice_number': self._normalize_invoice_number(invoice_number),
            'invoice_date': invoice_date,
            'amount_total': amount_total or 0.0,
            'currency_id': currency_id or self.env.company.currency_id.id,
            'picking_id': picking_id,
            'attachment_id': attachment_id,
            'document_type': document_type,
            'batch_uid': batch_uid,
            'batch_name': batch_name,
            'batch_index': batch_index or 0,
            'batch_total': batch_total or 0,
            'queued_at': fields.Datetime.now() if source in {'ocr', 'anaf'} or batch_uid else False,
        }
        job = self.create(vals)
        if payload:
            job._set_payload(payload)
        return job, True
