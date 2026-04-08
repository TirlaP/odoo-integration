# -*- coding: utf-8 -*-
import base64
import hashlib
import mimetypes
import shutil
import uuid

from odoo import _, fields, models
from odoo.exceptions import UserError


class InvoiceIngestUploadWizard(models.TransientModel):
    _name = 'invoice.ingest.upload.wizard'
    _description = 'Invoice Ingest Upload Wizard'

    pdf_file = fields.Binary('Document File')
    pdf_filename = fields.Char('Filename')
    upload_attachment_ids = fields.Many2many('ir.attachment', string='Documents')
    supplier_id = fields.Many2one('res.partner', string='Supplier (Optional)')

    def action_import_pdf(self):
        return self.action_import_document()

    def _document_checksum(self, document):
        attachment = document.get('attachment')
        if attachment and attachment.checksum:
            return attachment.checksum
        binary = document.get('binary') or b''
        return hashlib.sha1(binary).hexdigest() if binary else False

    def _find_existing_job_for_document(self, document):
        self.ensure_one()
        checksum = self._document_checksum(document)
        if not checksum:
            return self.env['invoice.ingest.job']

        Job = self.env['invoice.ingest.job']
        domain = [
            ('source', '=', 'ocr'),
            ('attachment_id', '!=', False),
            ('attachment_id.checksum', '=', checksum),
        ]
        existing = Job.search(domain + [('external_id', 'not ilike', '%:test:%')], order='id asc', limit=1)
        if existing:
            return existing
        return Job.search(domain, order='id asc', limit=1)

    def _open_existing_duplicate_document_action(self, job):
        self.ensure_one()
        job.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Duplicate Document',
                'message': _('This document was already imported.'),
                'type': 'warning',
                'sticky': True,
                'next': {
                    'type': 'ir.actions.act_window',
                    'name': 'Importuri facturi',
                    'res_model': 'invoice.ingest.job',
                    'res_id': job.id,
                    'views': [(False, 'form')],
                    'view_mode': 'form',
                    'target': 'current',
                },
            },
        }

    def _collect_uploaded_documents(self):
        self.ensure_one()
        documents = []
        if self.upload_attachment_ids:
            for attachment in self.upload_attachment_ids:
                binary = attachment.datas and base64.b64decode(attachment.datas) or b''
                filename = (attachment.name or 'invoice_document').strip()
                mimetype = attachment.mimetype or mimetypes.guess_type(filename)[0] or 'application/octet-stream'
                documents.append({
                    'attachment': attachment,
                    'filename': filename,
                    'binary': binary,
                    'mimetype': mimetype,
                })
        elif self.pdf_file:
            filename = (self.pdf_filename or 'invoice_document').strip()
            binary = base64.b64decode(self.pdf_file)
            mimetype = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
            attachment = self.env['ir.attachment'].create({
                'name': filename,
                'type': 'binary',
                'datas': self.pdf_file,
                'mimetype': mimetype,
                'res_model': 'invoice.ingest.upload.wizard',
                'res_id': self.id,
            })
            documents.append({
                'attachment': attachment,
                'filename': filename,
                'binary': binary,
                'mimetype': mimetype,
            })
        return documents

    def _queue_document_job(self, document, batch_uid, batch_name, batch_index, batch_total):
        self.ensure_one()
        filename = document['filename']
        binary = document['binary']
        mimetype = document['mimetype']
        attachment = document['attachment']
        kind = self.env['invoice.ingest.job']._detect_attachment_kind(
            binary,
            filename=filename,
            mimetype=mimetype,
        )
        if kind not in {'pdf', 'image'}:
            raise UserError(f'Please upload a PDF or image file. Invalid file: {filename}')
        if kind == 'image' and not shutil.which('tesseract'):
            raise UserError('Image OCR requires Tesseract OCR to be installed on the server.')

        job = self.env['invoice.ingest.job'].create({
            'name': f'OCR - {filename}',
            'source': 'ocr',
            'external_id': f'upload:{uuid.uuid4().hex}',
            'state': 'pending',
            'attachment_data': base64.b64encode(binary),
            'attachment_filename': filename,
            'partner_id': self.supplier_id.id if self.supplier_id else False,
            'ai_model': self.env['invoice.ingest.job']._default_ai_model(),
            'batch_uid': batch_uid,
            'batch_name': batch_name,
            'batch_index': batch_index,
            'batch_total': batch_total,
            'queued_at': fields.Datetime.now(),
        })

        attachment.write({'res_model': 'invoice.ingest.job', 'res_id': job.id})
        job.write({'attachment_id': attachment.id})

        job._audit_log(
            action='custom',
            description=f'Invoice OCR import queued for background processing: {job.display_name}',
            new_values={
                'source': job.source,
                'attachment_id': job.attachment_id.id if job.attachment_id else False,
                'partner_id': job.partner_id.id if job.partner_id else False,
                'external_id': job.external_id,
                'batch_uid': job.batch_uid,
                'batch_name': job.batch_name,
                'batch_index': job.batch_index,
                'batch_total': job.batch_total,
            },
        )
        return job

    def action_import_document(self):
        self.ensure_one()
        documents = self._collect_uploaded_documents()
        if not documents:
            raise UserError('Please upload at least one PDF or image first.')

        queued_documents = []
        duplicate_jobs = self.env['invoice.ingest.job']
        for document in documents:
            existing_job = self._find_existing_job_for_document(document)
            if existing_job:
                duplicate_jobs |= existing_job
                continue
            queued_documents.append(document)

        if not queued_documents:
            return self._open_existing_duplicate_document_action(duplicate_jobs[:1])

        batch_uid = uuid.uuid4().hex
        batch_name = (queued_documents[0]['filename'] or 'invoice batch').strip()
        async_batch = self.env['automotive.async.batch'].sudo().create({
            'name': batch_name,
            'job_type': 'invoice_ingest',
            'company_id': self.env.company.id,
            'requested_by_id': self.env.user.id,
        })
        jobs = self.env['invoice.ingest.job']
        total = len(queued_documents)
        for index, document in enumerate(queued_documents, start=1):
            job = self._queue_document_job(
                document,
                batch_uid=batch_uid,
                batch_name=batch_name,
                batch_index=index,
                batch_total=total,
            )
            job._enqueue_async_processing(
                batch=async_batch,
                batch_uid=batch_uid,
                batch_name=batch_name,
                priority=85,
                display_state='pending',
            )
            jobs |= job

        if len(jobs) == 1:
            job = jobs[:1]
            action = {
                'type': 'ir.actions.act_window',
                'name': 'Importuri facturi',
                'res_model': 'invoice.ingest.job',
                'res_id': job.id,
                'views': [(False, 'form')],
                'view_mode': 'form',
                'target': 'current',
            }
        else:
            action = self.env.ref('automotive_parts.action_invoice_ingest_jobs').read()[0]
            action.update({
                'domain': [('batch_uid', '=', batch_uid)],
                'views': [(False, 'list'), (False, 'form')],
                'view_mode': 'list,form',
                'target': 'current',
            })
        if not duplicate_jobs:
            return action

        skipped_count = len(duplicate_jobs)
        queued_count = len(jobs)
        duplicate_label = 'document' if skipped_count == 1 else 'documents'
        queued_label = 'document' if queued_count == 1 else 'documents'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Duplicate Documents Skipped',
                'message': _(
                    'Skipped %(skipped)s duplicate %(duplicate_label)s already imported. '
                    'Queued %(queued)s new %(queued_label)s for AI processing.'
                ) % {
                    'skipped': skipped_count,
                    'duplicate_label': duplicate_label,
                    'queued': queued_count,
                    'queued_label': queued_label,
                },
                'type': 'warning',
                'sticky': True,
                'next': action,
            },
        }
