# -*- coding: utf-8 -*-
import base64
import io
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from odoo import api, fields
from odoo.tests.common import TransactionCase, tagged
from odoo.exceptions import UserError, ValidationError
from odoo.tools.pdf import PdfFileReader, PdfFileWriter
from odoo.addons.automotive_parts.controllers.portal import CustomerPortal
from werkzeug.utils import redirect as werkzeug_redirect


@tagged('post_install', '-at_install')
class TestAutomotiveOperatorFlows(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supplier = cls.env['res.partner'].create({
            'name': 'Test Supplier',
            'supplier_rank': 1,
        })
        cls.auto_total_supplier = cls.env['res.partner'].create({
            'name': 'S.C. AD AUTO TOTAL S.R.L.',
            'supplier_rank': 1,
        })
        cls.product_tmpl = cls.env['product.template'].create({
            'name': 'Test Automotive Product',
            'default_code': 'AUTO-TEST-001',
            'barcode': '5941234567890',
            'list_price': 125.0,
            'standard_price': 90.0,
        })
        cls.product = cls.product_tmpl.product_variant_id

    def test_key_backend_actions_have_paths_and_names(self):
        expectations = {
            'sale.product_template_action': 'sale-products',
            'automotive_parts.action_automotive_stock_workspace': 'stocuri',
            'stock.action_picking_tree_incoming': 'receptii-nir',
            'stock.action_picking_tree_outgoing': 'livrari',
            'stock.action_picking_tree_internal': 'transferuri-interne',
            'stock.action_orderpoint': 'reaprovizionare',
            'automotive_parts.action_automotive_payment_allocations': 'alocari-plati',
            'automotive_parts.action_mechanic_portal_requests': 'cereri-mecanici',
            'automotive_parts.action_commercial_document_archive': 'documente-comerciale',
            'automotive_parts.action_invoice_ingest_jobs': 'importuri-facturi',
            'automotive_parts.action_invoice_ingest_upload_wizard': 'import-ai-facturi',
            'automotive_parts.action_anaf_efactura': 'configurare-anaf',
            'automotive_parts.action_audit_log': 'audit-logs',
        }
        for xmlid, expected_path in expectations.items():
            action = self.env.ref(xmlid)
            self.assertTrue(action.name, f'{xmlid} should always have a display name.')
            self.assertEqual(action.path, expected_path)

    def test_mechanic_portal_delivery_domain_uses_resolved_ids(self):
        mechanic_partner = self.env['res.partner'].create({
            'name': 'Portal Mechanic',
            'client_type': 'mechanic',
            'email': 'portal.mechanic@example.com',
        })
        mechanic_user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Portal Mechanic',
            'login': 'portal.mechanic@example.com',
            'email': 'portal.mechanic@example.com',
            'partner_id': mechanic_partner.id,
            'groups_id': [(6, 0, [self.env.ref('automotive_parts.group_mechanic_portal').id])],
        })
        customer = self.env['res.partner'].create({
            'name': 'Portal Customer',
            'customer_rank': 1,
        })
        order = self.env['sale.order'].create({
            'partner_id': customer.id,
            'mechanic_partner_id': mechanic_partner.id,
        })
        picking_type = self.env.ref('stock.picking_type_out')
        delivery = self.env['stock.picking'].create({
            'partner_id': customer.id,
            'sale_id': order.id,
            'picking_type_id': picking_type.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
        })
        controller = CustomerPortal()
        fake_request = SimpleNamespace(env=self.env, user=mechanic_user)

        with patch('odoo.addons.automotive_parts.controllers.portal.request', fake_request):
            domain = controller._prepare_mechanic_delivery_domain(mechanic_partner)

        self.assertEqual(domain, [('id', 'in', [delivery.id])])
        count = self.env['stock.picking'].with_user(mechanic_user).search_count(domain)
        self.assertEqual(count, 1)

    def test_mechanic_portal_status_helper_works_for_portal_user(self):
        mechanic_partner = self.env['res.partner'].create({
            'name': 'Portal Mechanic Status',
            'client_type': 'mechanic',
            'email': 'portal.mechanic.status@example.com',
        })
        mechanic_user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Portal Mechanic Status',
            'login': 'portal.mechanic.status@example.com',
            'email': 'portal.mechanic.status@example.com',
            'partner_id': mechanic_partner.id,
            'groups_id': [(6, 0, [self.env.ref('automotive_parts.group_mechanic_portal').id])],
        })
        customer = self.env['res.partner'].create({
            'name': 'Portal Customer Status',
            'customer_rank': 1,
        })
        order = self.env['sale.order'].create({
            'partner_id': customer.id,
            'mechanic_partner_id': mechanic_partner.id,
        })
        picking_type = self.env.ref('stock.picking_type_out')
        delivery = self.env['stock.picking'].create({
            'partner_id': customer.id,
            'sale_id': order.id,
            'picking_type_id': picking_type.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
            'state': 'assigned',
        })

        status = order.with_user(mechanic_user)._get_portal_mechanic_status()

        self.assertEqual(status['latest_picking'], delivery)
        self.assertEqual(status['delivery_label'], 'În magazin / în pregătire')

    def test_mechanic_portal_request_create_works_for_portal_user(self):
        mechanic_partner = self.env['res.partner'].create({
            'name': 'Portal Mechanic Request',
            'client_type': 'mechanic',
            'email': 'portal.mechanic.request@example.com',
        })
        mechanic_user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Portal Mechanic Request',
            'login': 'portal.mechanic.request@example.com',
            'email': 'portal.mechanic.request@example.com',
            'partner_id': mechanic_partner.id,
            'groups_id': [(6, 0, [self.env.ref('automotive_parts.group_mechanic_portal').id])],
        })
        customer = self.env['res.partner'].create({
            'name': 'Portal Customer Request',
            'customer_rank': 1,
        })
        order = self.env['sale.order'].create({
            'partner_id': customer.id,
            'mechanic_partner_id': mechanic_partner.id,
        })

        request_record = self.env['mechanic.portal.request'].with_user(mechanic_user).create({
            'partner_id': mechanic_partner.id,
            'request_user_id': mechanic_user.id,
            'sale_order_id': order.id,
            'request_type': 'general',
            'description': 'Need a part quote.',
        })

        self.assertTrue(request_record.name)
        self.assertNotEqual(request_record.name, '/')
        self.assertEqual(request_record.partner_id, mechanic_partner)
        self.assertEqual(request_record.sale_order_id, order)

    def test_mechanic_request_description_is_immutable_after_create(self):
        request_record = self.env['mechanic.portal.request'].create({
            'partner_id': self.env['res.partner'].create({
                'name': 'Immutable Mechanic',
                'client_type': 'mechanic',
            }).id,
            'request_user_id': self.env.user.id,
            'request_type': 'general',
            'description': 'Initial request body',
        })

        with self.assertRaises(ValidationError):
            request_record.write({'description': 'Changed body'})

    def test_mechanic_portal_reply_posts_message_and_reopens_request(self):
        mechanic_partner = self.env['res.partner'].create({
            'name': 'Portal Mechanic Reply',
            'client_type': 'mechanic',
            'email': 'portal.mechanic.reply@example.com',
        })
        mechanic_user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Portal Mechanic Reply',
            'login': 'portal.mechanic.reply@example.com',
            'email': 'portal.mechanic.reply@example.com',
            'partner_id': mechanic_partner.id,
            'groups_id': [(6, 0, [self.env.ref('automotive_parts.group_mechanic_portal').id])],
        })
        request_record = self.env['mechanic.portal.request'].create({
            'partner_id': mechanic_partner.id,
            'request_user_id': mechanic_user.id,
            'request_type': 'general',
            'description': 'Initial request body',
            'state': 'waiting_customer',
        })

        request_record.with_user(mechanic_user).action_portal_reply('Here is my reply')

        self.assertEqual(request_record.state, 'in_progress')
        last_message = request_record.message_ids.sorted('id')[-1]
        self.assertIn('Here is my reply', last_message.body)

    def test_mechanic_document_counts_use_unified_workspace_total(self):
        mechanic_partner = self.env['res.partner'].create({
            'name': 'Portal Mechanic Documents',
            'client_type': 'mechanic',
            'email': 'portal.mechanic.documents@example.com',
        })
        mechanic_user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Portal Mechanic Documents',
            'login': 'portal.mechanic.documents@example.com',
            'email': 'portal.mechanic.documents@example.com',
            'partner_id': mechanic_partner.id,
            'groups_id': [(6, 0, [self.env.ref('automotive_parts.group_mechanic_portal').id])],
        })
        customer = self.env['res.partner'].create({
            'name': 'Portal Customer Documents',
            'customer_rank': 1,
        })
        order = self.env['sale.order'].create({
            'partner_id': customer.id,
            'mechanic_partner_id': mechanic_partner.id,
        })
        picking_type = self.env.ref('stock.picking_type_out')
        delivery = self.env['stock.picking'].create({
            'partner_id': customer.id,
            'sale_id': order.id,
            'picking_type_id': picking_type.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
            'state': 'assigned',
        })
        attachment = self.env['ir.attachment'].create({
            'name': 'mechanic-doc.pdf',
            'type': 'binary',
            'datas': base64.b64encode(b'%PDF-1.4\n% mechanic portal archive\n'),
            'mimetype': 'application/pdf',
        })
        self.env['commercial.document.archive'].create({
            'state': 'archived',
            'document_type': 'delivery_note',
            'partner_id': mechanic_partner.id,
            'attachment_id': attachment.id,
        })

        controller = CustomerPortal()
        fake_request = SimpleNamespace(env=self.env, user=mechanic_user)

        with patch('odoo.addons.automotive_parts.controllers.portal.request', fake_request):
            counts = controller._get_mechanic_document_counts(mechanic_partner)

        self.assertEqual(counts['invoices'], 0)
        self.assertEqual(counts['deliveries'], 1)
        self.assertEqual(counts['archived'], 1)
        self.assertEqual(counts['all'], 2)

    def test_mechanic_payments_route_redirects_to_documents(self):
        mechanic_partner = self.env['res.partner'].create({
            'name': 'Portal Mechanic Payments Redirect',
            'client_type': 'mechanic',
            'email': 'portal.mechanic.redirect@example.com',
        })
        mechanic_user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Portal Mechanic Payments Redirect',
            'login': 'portal.mechanic.redirect@example.com',
            'email': 'portal.mechanic.redirect@example.com',
            'partner_id': mechanic_partner.id,
            'groups_id': [(6, 0, [self.env.ref('automotive_parts.group_mechanic_portal').id])],
        })
        controller = CustomerPortal()
        fake_request = SimpleNamespace(
            env=self.env,
            user=mechanic_user,
            redirect=lambda url: werkzeug_redirect(url),
        )

        with patch('odoo.addons.automotive_parts.controllers.portal.request', fake_request):
            redirect = controller.portal_my_mechanic_payments()

        self.assertEqual(redirect.status_code, 302)
        self.assertEqual(redirect.location, '/my/mechanic/documents')

    def test_invoice_upload_wizard_queues_pdf_job(self):
        wizard = self.env['invoice.ingest.upload.wizard'].create({
            'supplier_id': self.supplier.id,
            'pdf_file': base64.b64encode(b'%PDF-1.4\n% automotive test\n'),
            'pdf_filename': 'supplier_invoice.pdf',
        })

        action = wizard.action_import_document()

        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(action['res_model'], 'invoice.ingest.job')
        self.assertEqual(action['view_mode'], 'form')
        self.assertEqual(action['views'], [(False, 'form')])

        job = self.env['invoice.ingest.job'].search(
            [('source', '=', 'ocr'), ('partner_id', '=', self.supplier.id)],
            order='id desc',
            limit=1,
        )
        self.assertTrue(job, 'The upload wizard should create an OCR ingest job.')
        self.assertEqual(job.batch_total, 1)
        self.assertEqual(job.batch_index, 1)
        self.assertEqual(job.state, 'pending')
        self.assertTrue(job.attachment_id, 'The ingest job should keep the uploaded file as an attachment.')
        self.assertTrue(job.attachment_data, 'The ingest job should keep a DB-backed copy of the uploaded file bytes.')
        self.assertEqual(job.attachment_filename, 'supplier_invoice.pdf')

        async_job = self.env['automotive.async.job'].search(
            [
                ('target_model', '=', 'invoice.ingest.job'),
                ('target_method', '=', '_process_ingest_job'),
                ('target_res_id', '=', job.id),
            ],
            order='id desc',
            limit=1,
        )
        self.assertTrue(async_job, 'Queueing an invoice import should enqueue a background job.')
        self.assertEqual(async_job.state, 'queued')
        self.assertEqual(async_job.progress, 0.0)
        self.assertEqual(async_job.progress_message, 'Queued, waiting for worker')
        self.assertEqual(job.active_async_job_id, async_job)
        self.assertEqual(job.async_progress_percent, 0.0)
        self.assertEqual(job.async_progress_message, 'Queued, waiting for worker')
        self.assertEqual(action['res_id'], job.id)

    def test_invoice_upload_wizard_multiple_documents_opens_batch_list(self):
        attachment_one = self.env['ir.attachment'].create({
            'name': 'supplier_invoice_1.pdf',
            'type': 'binary',
            'datas': base64.b64encode(b'%PDF-1.4\n% automotive test one\n'),
            'mimetype': 'application/pdf',
        })
        attachment_two = self.env['ir.attachment'].create({
            'name': 'supplier_invoice_2.pdf',
            'type': 'binary',
            'datas': base64.b64encode(b'%PDF-1.4\n% automotive test two\n'),
            'mimetype': 'application/pdf',
        })
        wizard = self.env['invoice.ingest.upload.wizard'].create({
            'supplier_id': self.supplier.id,
            'upload_attachment_ids': [(6, 0, [attachment_one.id, attachment_two.id])],
        })

        action = wizard.action_import_document()

        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(action['res_model'], 'invoice.ingest.job')
        self.assertEqual(action['target'], 'current')
        self.assertEqual(action['domain'][0][0], 'batch_uid')
        self.assertEqual(action['views'], [(False, 'list'), (False, 'form')])

    def test_same_uploaded_file_opens_existing_ocr_job(self):
        payload = base64.b64encode(b'%PDF-1.4\n% identical content\n')
        wizard_one = self.env['invoice.ingest.upload.wizard'].create({
            'supplier_id': self.supplier.id,
            'pdf_file': payload,
            'pdf_filename': 'same_file.pdf',
        })
        action_one = wizard_one.action_import_document()
        first_job = self.env['invoice.ingest.job'].browse(action_one['res_id'])
        job_count_before = self.env['invoice.ingest.job'].search_count([
            ('source', '=', 'ocr'),
            ('attachment_id', '!=', False),
        ])

        wizard_two = self.env['invoice.ingest.upload.wizard'].create({
            'supplier_id': self.supplier.id,
            'pdf_file': payload,
            'pdf_filename': 'same_file_again.pdf',
        })
        action_two = wizard_two.action_import_document()

        self.assertEqual(action_two['type'], 'ir.actions.client')
        self.assertEqual(action_two['tag'], 'display_notification')
        self.assertEqual(action_two['params']['title'], 'Duplicate Document')
        self.assertEqual(action_two['params']['message'], 'This document was already imported.')
        self.assertEqual(action_two['params']['next']['res_model'], 'invoice.ingest.job')
        self.assertEqual(action_two['params']['next']['res_id'], first_job.id)
        self.assertEqual(
            self.env['invoice.ingest.job'].search_count([
                ('source', '=', 'ocr'),
                ('attachment_id', '!=', False),
            ]),
            job_count_before,
        )

    def test_multi_upload_skips_duplicate_and_queues_other_documents(self):
        duplicate_attachment = self.env['ir.attachment'].create({
            'name': 'duplicate.pdf',
            'type': 'binary',
            'datas': base64.b64encode(b'%PDF-1.4\n% duplicate content\n'),
            'mimetype': 'application/pdf',
        })
        original_job = self.env['invoice.ingest.job'].create({
            'name': 'OCR - duplicate.pdf',
            'source': 'ocr',
            'external_id': 'upload:test:duplicate',
            'state': 'pending',
            'attachment_filename': 'duplicate.pdf',
            'attachment_id': duplicate_attachment.id,
        })
        duplicate_attachment.write({
            'res_model': 'invoice.ingest.job',
            'res_id': original_job.id,
        })
        unique_attachment_one = self.env['ir.attachment'].create({
            'name': 'unique_1.pdf',
            'type': 'binary',
            'datas': base64.b64encode(b'%PDF-1.4\n% unique content one\n'),
            'mimetype': 'application/pdf',
        })
        unique_attachment_two = self.env['ir.attachment'].create({
            'name': 'unique_2.pdf',
            'type': 'binary',
            'datas': base64.b64encode(b'%PDF-1.4\n% unique content two\n'),
            'mimetype': 'application/pdf',
        })
        wizard = self.env['invoice.ingest.upload.wizard'].create({
            'supplier_id': self.supplier.id,
            'upload_attachment_ids': [(6, 0, [
                duplicate_attachment.id,
                unique_attachment_one.id,
                unique_attachment_two.id,
            ])],
        })
        job_count_before = self.env['invoice.ingest.job'].search_count([
            ('source', '=', 'ocr'),
            ('attachment_id', '!=', False),
        ])

        action = wizard.action_import_document()

        self.assertEqual(action['type'], 'ir.actions.client')
        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(action['params']['title'], 'Duplicate Documents Skipped')
        self.assertIn('Skipped 1 duplicate document', action['params']['message'])
        self.assertIn('Queued 2 new documents', action['params']['message'])
        self.assertEqual(action['params']['next']['res_model'], 'invoice.ingest.job')
        self.assertEqual(action['params']['next']['target'], 'current')
        self.assertEqual(action['params']['next']['views'], [(False, 'list'), (False, 'form')])

        batch_uid = action['params']['next']['domain'][0][2]
        queued_jobs = self.env['invoice.ingest.job'].search([('batch_uid', '=', batch_uid)], order='id asc')
        self.assertEqual(len(queued_jobs), 2)
        self.assertEqual(set(queued_jobs.mapped('attachment_filename')), {'unique_1.pdf', 'unique_2.pdf'})
        self.assertFalse(queued_jobs.filtered(lambda job: job.attachment_id == duplicate_attachment))
        self.assertEqual(
            self.env['invoice.ingest.job'].search_count([
                ('source', '=', 'ocr'),
                ('attachment_id', '!=', False),
            ]),
            job_count_before + 2,
        )

    def test_invoice_ingest_cron_skips_empty_manual_jobs(self):
        manual_job = self.env['invoice.ingest.job'].create({
            'name': 'Manual Placeholder',
            'source': 'manual',
            'state': 'pending',
        })

        queued = self.env['invoice.ingest.job'].cron_process_jobs()

        self.assertEqual(queued, 0)
        self.assertFalse(
            self.env['automotive.async.job'].search_count([
                ('target_model', '=', 'invoice.ingest.job'),
                ('target_res_id', '=', manual_job.id),
            ]),
            'Empty manual jobs should not be sent to the async OCR queue.',
        )

    def test_invoice_ingest_cron_processes_async_queue_as_fallback(self):
        attachment = self.env['ir.attachment'].create({
            'name': 'fallback_queue.pdf',
            'datas': base64.b64encode(b'%PDF-1.4\n% fallback queue\n'),
            'res_model': 'invoice.ingest.job',
            'type': 'binary',
            'mimetype': 'application/pdf',
        })
        job = self.env['invoice.ingest.job'].create({
            'name': 'OCR Fallback Queue',
            'source': 'ocr',
            'state': 'pending',
            'attachment_id': attachment.id,
            'external_id': 'fallback-queue-checksum',
        })

        def fake_process(recordset, raise_on_error=False):
            recordset.write({
                'state': 'needs_review',
                'started_at': fields.Datetime.now(),
                'finished_at': fields.Datetime.now(),
                'error': False,
            })
            return True

        with patch.object(type(job), '_process_ingest_job', fake_process):
            processed = self.env['invoice.ingest.job'].cron_process_jobs()

        async_job = self.env['automotive.async.job'].search(
            [
                ('target_model', '=', 'invoice.ingest.job'),
                ('target_method', '=', '_process_ingest_job'),
                ('target_res_id', '=', job.id),
            ],
            order='id desc',
            limit=1,
        )
        self.assertGreaterEqual(processed, 1)
        self.assertTrue(async_job, 'Invoice ingest cron should enqueue an async job for OCR imports.')
        self.assertEqual(async_job.state, 'done')
        self.assertEqual(job.state, 'needs_review')

    def test_invoice_ingest_cron_processes_existing_queued_async_job(self):
        attachment = self.env['ir.attachment'].create({
            'name': 'existing_queue.pdf',
            'datas': base64.b64encode(b'%PDF-1.4\n% existing queue\n'),
            'res_model': 'invoice.ingest.job',
            'type': 'binary',
            'mimetype': 'application/pdf',
        })
        job = self.env['invoice.ingest.job'].create({
            'name': 'OCR Existing Queue',
            'source': 'ocr',
            'state': 'pending',
            'attachment_id': attachment.id,
            'external_id': 'existing-queue-checksum',
        })
        async_job = self.env['automotive.async.job'].enqueue_job(
            'invoice_ingest',
            name='Process OCR Existing Queue',
            payload={'invoice_ingest_job_id': job.id},
            source=job,
            target_model='invoice.ingest.job',
            target_method='_process_ingest_job',
            target_res_id=job.id,
            priority=90,
        )

        def fake_process(recordset, raise_on_error=False):
            recordset.write({
                'state': 'needs_review',
                'started_at': fields.Datetime.now(),
                'finished_at': fields.Datetime.now(),
                'error': False,
            })
            return True

        with patch.object(type(job), '_process_ingest_job', fake_process):
            processed = self.env['invoice.ingest.job'].cron_process_jobs()

        async_job.invalidate_recordset()
        job.invalidate_recordset()
        self.assertGreaterEqual(processed, 1)
        self.assertEqual(async_job.state, 'done')
        self.assertEqual(job.state, 'needs_review')

    def test_invoice_ingest_reads_db_backed_upload_bytes_when_attachment_is_missing(self):
        payload = b'%PDF-1.4\n% db-backed upload\n'
        job = self.env['invoice.ingest.job'].create({
            'name': 'OCR Stored Upload',
            'source': 'ocr',
            'state': 'pending',
            'attachment_data': base64.b64encode(payload),
            'attachment_filename': 'stored.pdf',
        })

        self.assertEqual(job._get_attachment_binary(), payload)
        self.assertEqual(job._extract_pdf_text(), '')

    def test_async_job_records_ingest_failures_in_last_error(self):
        job = self.env['invoice.ingest.job'].create({
            'name': 'OCR Missing File',
            'source': 'ocr',
            'state': 'pending',
            'attachment_id': self.env['ir.attachment'].create({
                'name': 'missing.pdf',
                'type': 'binary',
                'mimetype': 'application/pdf',
                'res_model': 'invoice.ingest.job',
            }).id,
            'external_id': 'missing-file-checksum',
        })

        async_job = job._enqueue_async_processing()
        processed = async_job._process_one(force=True)

        self.assertFalse(processed)
        self.assertEqual(job.state, 'failed')
        self.assertIn('Re-upload the document', job.error or '')
        self.assertEqual(async_job.state, 'queued')
        self.assertEqual(async_job.last_error_type, 'UserError')
        self.assertIn('Re-upload the document', async_job.last_error or '')
        runtime_log = self.env['automotive.runtime.log'].search(
            [('event', '=', 'automotive_async_job_failed'), ('related_res_id', '=', job.id)],
            order='id desc',
            limit=1,
        )
        self.assertTrue(runtime_log)
        self.assertEqual(runtime_log.category, 'async_job')
        self.assertIn('Re-upload the document', runtime_log.message or '')

    def test_async_ocr_processing_commits_running_state_before_extraction(self):
        attachment = self.env['ir.attachment'].create({
            'name': 'running_state.pdf',
            'datas': base64.b64encode(b'%PDF-1.4\n% running state\n'),
            'res_model': 'invoice.ingest.job',
            'type': 'binary',
            'mimetype': 'application/pdf',
        })
        job = self.env['invoice.ingest.job'].create({
            'name': 'OCR Running Visibility',
            'source': 'ocr',
            'state': 'pending',
            'attachment_id': attachment.id,
            'external_id': 'running-state-checksum',
        })
        async_job = job._enqueue_async_processing()
        observed = {}

        def fake_extract(recordset):
            with self.registry.cursor() as other_cr:
                other_env = api.Environment(other_cr, self.env.uid, {})
                other_job = other_env['invoice.ingest.job'].browse(job.id)
                observed['state'] = other_job.state
                observed['started_at'] = bool(other_job.started_at)
                observed['stage'] = other_job.async_progress_message
                observed['progress'] = other_job.async_progress_percent
            recordset.write({
                'state': 'needs_review',
                'finished_at': fields.Datetime.now(),
                'error': False,
            })
            return True

        with patch.object(type(job), 'action_extract_with_openai', fake_extract):
            processed = async_job._process_one(force=True)

        self.assertTrue(processed)
        self.assertEqual(observed.get('state'), 'running')
        self.assertTrue(observed.get('started_at'))
        self.assertEqual(observed.get('stage'), 'Worker claimed, starting import')
        self.assertGreaterEqual(observed.get('progress') or 0.0, 1.0)

    def test_async_job_claim_switches_invoice_ingest_to_running(self):
        attachment = self.env['ir.attachment'].create({
            'name': 'claim_running.pdf',
            'datas': base64.b64encode(b'%PDF-1.4\n% claim running\n'),
            'res_model': 'invoice.ingest.job',
            'type': 'binary',
            'mimetype': 'application/pdf',
        })
        job = self.env['invoice.ingest.job'].create({
            'name': 'OCR Claim Visibility',
            'source': 'ocr',
            'state': 'pending',
            'attachment_id': attachment.id,
            'external_id': 'claim-running-checksum',
        })
        async_job = job._enqueue_async_processing()

        claim_ids = self.env['automotive.async.job']._claim_job_ids(1)

        self.assertIn(async_job.id, claim_ids)
        async_job.invalidate_recordset()
        job.invalidate_recordset()
        self.assertEqual(async_job.state, 'running')
        self.assertEqual(job.state, 'running')
        self.assertEqual(async_job.progress_message, 'Worker claimed, starting import')
        self.assertEqual(job.active_async_job_id, async_job)
        self.assertEqual(job.async_progress_message, 'Worker claimed, starting import')
        self.assertGreaterEqual(job.async_progress_percent, 1.0)

    def test_enqueue_async_processing_triggers_async_cron_immediately(self):
        attachment = self.env['ir.attachment'].create({
            'name': 'trigger_cron.pdf',
            'datas': base64.b64encode(b'%PDF-1.4\n% trigger cron\n'),
            'res_model': 'invoice.ingest.job',
            'type': 'binary',
            'mimetype': 'application/pdf',
        })
        job = self.env['invoice.ingest.job'].create({
            'name': 'OCR Trigger Cron',
            'source': 'ocr',
            'state': 'pending',
            'attachment_id': attachment.id,
            'external_id': 'trigger-cron-checksum',
        })
        cron = self.env.ref('automotive_parts.ir_cron_automotive_async_jobs')
        original_trigger = type(cron)._trigger
        trigger_calls = []

        def traced_trigger(recordset, at=None):
            if cron.id in recordset.ids:
                trigger_calls.append(at)
            return original_trigger(recordset, at=at)

        with patch.object(type(cron), '_trigger', autospec=True, side_effect=traced_trigger):
            async_job = job._enqueue_async_processing()

        self.assertTrue(async_job)
        self.assertEqual(async_job.state, 'queued')
        self.assertEqual(len(trigger_calls), 1)

    def test_async_job_completes_after_live_progress_updates_same_row(self):
        attachment = self.env['ir.attachment'].create({
            'name': 'progress_commit.pdf',
            'datas': base64.b64encode(b'%PDF-1.4\n% progress commit\n'),
            'res_model': 'invoice.ingest.job',
            'type': 'binary',
            'mimetype': 'application/pdf',
        })
        job = self.env['invoice.ingest.job'].create({
            'name': 'OCR Progress Finalization',
            'source': 'ocr',
            'state': 'pending',
            'attachment_id': attachment.id,
            'external_id': 'progress-finalization-checksum',
        })
        async_job = job._enqueue_async_processing()
        events = []
        original_write = type(async_job).write

        def fake_extract(recordset):
            events.append('extract')
            recordset._report_async_progress(95.0, 'Saving extracted invoice lines')
            recordset.write({
                'state': 'needs_review',
                'finished_at': fields.Datetime.now(),
                'error': False,
            })
            return True

        def fake_commit():
            events.append('commit')
            return True

        def traced_write(recordset, vals):
            if getattr(recordset, '_name', None) == 'automotive.async.job' and async_job.id in recordset.ids:
                if vals.get('state') == 'done':
                    events.append('done_write')
            return original_write(recordset, vals)

        with patch.object(type(job), 'action_extract_with_openai', fake_extract), \
             patch.object(async_job.env.cr, 'commit', side_effect=fake_commit), \
             patch.object(type(async_job), 'write', autospec=True, side_effect=traced_write):
            processed = async_job._process_one(force=True)

        self.assertTrue(processed)
        async_job.invalidate_recordset()
        job.invalidate_recordset()
        self.assertEqual(async_job.state, 'done')
        self.assertEqual(async_job.progress, 100.0)
        self.assertEqual(async_job.progress_message, 'Done')
        self.assertEqual(job.state, 'needs_review')
        self.assertIn('extract', events)
        self.assertIn('done_write', events)
        self.assertLess(events.index('extract'), events.index('done_write'))
        self.assertLess(events.index('commit', events.index('extract')), events.index('done_write'))

    def test_stale_running_async_job_requeues_back_to_pending(self):
        attachment = self.env['ir.attachment'].create({
            'name': 'stale_running.pdf',
            'datas': base64.b64encode(b'%PDF-1.4\n% stale running\n'),
            'res_model': 'invoice.ingest.job',
            'type': 'binary',
            'mimetype': 'application/pdf',
        })
        job = self.env['invoice.ingest.job'].create({
            'name': 'OCR Stale Requeue',
            'source': 'ocr',
            'state': 'pending',
            'attachment_id': attachment.id,
            'external_id': 'stale-requeue-checksum',
        })
        async_job = job._enqueue_async_processing()
        self.env['automotive.async.job']._claim_job_ids(1)
        async_job.invalidate_recordset()
        job.invalidate_recordset()
        self.assertEqual(async_job.state, 'running')
        self.assertEqual(job.state, 'running')

        stale_write_date = fields.Datetime.to_string(fields.Datetime.now() - timedelta(minutes=61))
        self.env.cr.execute(
            """
            UPDATE automotive_async_job
               SET progress = 95,
                   progress_message = %s,
                   write_date = %s
             WHERE id = %s
            """,
            ['Saving extracted invoice lines', stale_write_date, async_job.id],
        )

        stale_jobs = self.env['automotive.async.job']._requeue_stale_running_jobs(timeout_minutes=30)

        self.assertIn(async_job, stale_jobs)
        async_job.invalidate_recordset()
        job.invalidate_recordset()
        self.assertEqual(async_job.state, 'queued')
        self.assertEqual(async_job.progress, 0.0)
        self.assertEqual(async_job.progress_message, 'Queued, waiting for worker')
        self.assertEqual(job.state, 'pending')
        self.assertEqual(job.async_progress_message, 'Queued, waiting for worker')

    def test_async_job_recovery_requeues_unexpected_crash_instead_of_sticking_running(self):
        attachment = self.env['ir.attachment'].create({
            'name': 'crash_requeue.pdf',
            'datas': base64.b64encode(b'%PDF-1.4\n% crash requeue\n'),
            'res_model': 'invoice.ingest.job',
            'type': 'binary',
            'mimetype': 'application/pdf',
        })
        job = self.env['invoice.ingest.job'].create({
            'name': 'OCR Unexpected Crash',
            'source': 'ocr',
            'state': 'pending',
            'attachment_id': attachment.id,
            'external_id': 'unexpected-crash-checksum',
        })
        async_job = job._enqueue_async_processing()
        self.env['automotive.async.job']._claim_job_ids(1)
        async_job.invalidate_recordset()
        job.invalidate_recordset()
        self.assertEqual(async_job.state, 'running')
        self.assertEqual(job.state, 'running')

        self.env['automotive.async.job']._recover_unexpected_job_crash(
            async_job.id,
            RuntimeError('post-process crash'),
        )

        async_job.invalidate_recordset()
        job.invalidate_recordset()
        self.assertEqual(async_job.state, 'queued')
        self.assertEqual(async_job.progress, 0.0)
        self.assertEqual(async_job.progress_message, 'Queued, waiting for worker')
        self.assertEqual(async_job.last_error_type, 'RuntimeError')
        self.assertIn('post-process crash', async_job.last_error or '')
        self.assertEqual(job.state, 'pending')
        runtime_log = self.env['automotive.runtime.log'].search(
            [('event', '=', 'automotive_async_job_failed'), ('related_res_id', '=', job.id)],
            order='id desc',
            limit=1,
        )
        self.assertTrue(runtime_log)
        self.assertIn('post-process crash', runtime_log.message or '')

    def test_async_job_cancels_missing_invoice_ingest_target_instead_of_failing(self):
        async_job = self.env['automotive.async.job'].enqueue_job(
            'invoice_ingest',
            name='Missing invoice ingest target',
            payload={'invoice_ingest_job_id': 999999},
            target_model='invoice.ingest.job',
            target_method='_process_ingest_job',
            target_res_id=999999,
        )

        processed = async_job._process_one(force=True)

        self.assertFalse(processed)
        async_job.invalidate_recordset()
        self.assertEqual(async_job.state, 'cancelled')
        self.assertEqual(async_job.last_error, False)
        self.assertEqual(async_job.last_error_type, False)
        self.assertEqual(async_job.progress_message, 'Cancelled because the target record was deleted.')
        runtime_log = self.env['automotive.runtime.log'].search(
            [('event', '=', 'automotive_async_job_failed'), ('related_res_id', '=', 999999)],
            order='id desc',
            limit=1,
        )
        self.assertFalse(runtime_log)

    def test_invoice_extract_reports_real_async_progress_milestones(self):
        attachment = self.env['ir.attachment'].create({
            'name': 'milestones.pdf',
            'datas': base64.b64encode(b'%PDF-1.4\n% milestones\n'),
            'res_model': 'invoice.ingest.job',
            'type': 'binary',
            'mimetype': 'application/pdf',
        })
        job = self.env['invoice.ingest.job'].create({
            'name': 'OCR Milestone Progress',
            'source': 'ocr',
            'state': 'running',
            'attachment_id': attachment.id,
            'external_id': 'milestone-progress-checksum',
        })
        milestones = []

        class FakePdfReader:
            def __init__(self, *_args, **_kwargs):
                self.pages = []

        class FakeResponse:
            status_code = 200
            text = ''

            def json(self):
                return {
                    'choices': [{
                        'message': {
                            'content': json.dumps({
                                'supplier_name': self_supplier.name,
                                'invoice_number': 'INV-2026-001',
                                'invoice_date': '2026-04-08',
                                'invoice_currency': self_env.company.currency_id.name,
                                'vat_rate': 19,
                                'amount_total': 119.0,
                                'confidence': 93,
                                'invoice_lines': [{
                                    'quantity': 1,
                                    'product_code': 'AUTO-TEST-001',
                                    'product_code_raw': 'AUTO-TEST-001',
                                    'product_description': 'Test Automotive Product',
                                    'supplier_brand': 'TEST',
                                    'unit_price': 100.0,
                                }],
                            }),
                        },
                    }],
                }

        self_env = self.env
        self_supplier = self.supplier

        def capture_progress(_recordset, progress, message):
            milestones.append((progress, message))
            return True

        def fake_resolve(
            _recordset,
            raw_code='',
            product_code='',
            product_description='',
            supplier=None,
            supplier_brand='',
            extra_codes=None,
            line_index=None,
            line_total=None,
        ):
            return {
                'product_code_raw': raw_code or product_code or 'AUTO-TEST-001',
                'product_code': product_code or 'AUTO-TEST-001',
                'supplier_brand': supplier_brand or 'TEST',
                'supplier_brand_id': False,
                'matched_product_id': self.product.id,
                'matched_product_name': self.product.display_name,
                'match_status': 'matched',
                'match_method': 'exact:default_code',
                'match_confidence': 100.0,
            }

        with patch.object(type(job), '_report_async_progress', autospec=True, side_effect=capture_progress), \
             patch.object(type(job), '_get_openai_api_key', return_value='test-key'), \
             patch.object(type(job), '_detect_attachment_kind', return_value='pdf'), \
             patch.object(type(job), '_get_attachment_binary', return_value=b'%PDF-1.4\n% milestone progress\n'), \
             patch.object(type(job), '_extract_pdf_text_with_pdftotext', return_value=''), \
             patch('odoo.addons.automotive_parts.models.invoice_ingest.PdfReader', FakePdfReader), \
             patch.object(type(job), '_extract_pdf_text_with_ocr', return_value='Furnizor  Test Supplier\nFactura INV-2026-001\nTotal de plata 119.00'), \
             patch.object(type(job), '_extract_invoice_totals_from_text', return_value={'amount_total': 119.0, 'vat_rate': 19.0}), \
             patch.object(type(job), '_extract_invoice_header_from_text', return_value={'supplier_name': self.supplier.name, 'invoice_number': 'INV-2026-001', 'invoice_date': '2026-04-08'}), \
             patch.object(type(job), '_extract_invoice_lines_from_text', return_value=[]), \
             patch.object(type(job), '_get_or_create_supplier_partner', return_value=self.supplier), \
             patch.object(type(job), '_resolve_line_match_data', autospec=True, side_effect=fake_resolve), \
             patch('odoo.addons.automotive_parts.models.invoice_ingest.requests.post', return_value=FakeResponse()):
            job.action_extract_with_openai()

        self.assertEqual(
            milestones,
            [
                (10.0, 'Reading attachment'),
                (25.0, 'Extracting PDF text'),
                (40.0, 'Running OCR fallback'),
                (60.0, 'Calling OpenAI extraction'),
                (80.0, 'Matching products and normalizing lines'),
                (95.0, 'Saving extracted invoice lines'),
            ],
        )

    def test_invoice_match_runtime_logs_capture_80_percent_phases(self):
        attachment = self.env['ir.attachment'].create({
            'name': 'match-trace.pdf',
            'datas': base64.b64encode(b'%PDF-1.4\n% match trace\n'),
            'res_model': 'invoice.ingest.job',
            'type': 'binary',
            'mimetype': 'application/pdf',
        })
        job = self.env['invoice.ingest.job'].create({
            'name': 'OCR Match Trace',
            'source': 'ocr',
            'state': 'running',
            'attachment_id': attachment.id,
            'external_id': 'match-trace-checksum',
        })
        async_job = self.env['automotive.async.job'].enqueue_job(
            'invoice_ingest',
            name='Match Trace Async',
            payload={'invoice_ingest_job_id': job.id},
            target_model='invoice.ingest.job',
            target_method='_process_ingest_job',
            target_res_id=job.id,
        )
        empty_product = self.env['product.product']

        with patch.object(type(job), '_search_code_fields_with_scopes', side_effect=[
            (empty_product, {}),
            (empty_product, {}),
            (self.product, {
                'method': 'ilike:default_code supplier',
                'matched_code': 'AUTO-TRACE-001',
                'confidence': 86.0,
            }),
        ]), patch.object(type(job), '_match_by_catalog_lookup', return_value=(empty_product, '')):
            resolved = job.with_context(automotive_async_job_id=async_job.id)._resolve_line_match_data(
                raw_code='AUTO TRACE 001',
                product_code='AUTO-TRACE-001',
                product_description='Traceable Automotive Product',
                supplier=self.supplier,
                supplier_brand='TRACE',
                line_index=1,
                line_total=1,
            )

        self.assertEqual(resolved['match_status'], 'matched')
        runtime_logs = self.env['automotive.runtime.log'].search(
            [('event', '=', 'invoice_ingest_match_trace'), ('related_res_id', '=', job.id)],
            order='id asc',
        )
        self.assertTrue(runtime_logs)
        phases = [json.loads(log.payload_json).get('phase') for log in runtime_logs]
        self.assertEqual(
            phases,
            ['line_start', 'exact_start', 'lookup_start', 'trim_start', 'ilike_start', 'matched', 'line_complete'],
        )

    def test_replace_lines_from_normalized_reuses_precomputed_match_data(self):
        job = self.env['invoice.ingest.job'].create({
            'name': 'OCR Reuse Normalized Matches',
            'source': 'ocr',
            'state': 'needs_review',
            'partner_id': self.supplier.id,
        })
        normalized_lines = [{
            'quantity': 1,
            'product_code_raw': 'AUTO-TEST-001',
            'product_code': 'AUTO-TEST-001',
            'supplier_brand': 'TEST',
            'supplier_brand_id': False,
            'product_description': 'Test Automotive Product',
            'unit_price': 100.0,
            'vat_rate': 19.0,
            'matched_product_id': self.product.id,
            'match_method': 'exact:default_code',
            'match_confidence': 100.0,
        }]

        with patch.object(type(job), '_resolve_line_match_data', side_effect=AssertionError('save step should not re-match')):
            job._replace_lines_from_normalized(normalized_lines)

        self.assertEqual(len(job.line_ids), 1)
        self.assertEqual(job.line_ids.product_id, self.product)
        self.assertEqual(job.line_ids.match_method, 'exact:default_code')

    def test_extract_invoice_lines_from_text_parses_crystal_reports_layout(self):
        job = self.env['invoice.ingest.job'].create({
            'name': 'OCR Crystal Reports Parser',
            'source': 'ocr',
        })
        sample_text = """
     1        NF-00375555DREIS SET STERGATOR FLAT                                  SET              2                         6.21                         12.42                       2.61
               BLADE 550/550MM - DREISSNER
     2        C 32 191 FILTRU AER - MANN-FILTER                                    BUC              1                       38.59                          38.59                       8.10
               NC=84213100 CPV=42913000-9
     3        VO-LS-1870 VO-LS-1870 BRAT/BIELETA                                   BUC              10                      29.94                         299.40                      62.87
               SUSPENSIE STABILIZATOR MOOG
               NC=87088099
Data sc:        06/05/2026
"""

        lines = job._extract_invoice_lines_from_text(sample_text, default_vat_rate=21.0)

        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0]['quantity'], 2.0)
        self.assertEqual(lines[0]['unit_price'], 6.21)
        self.assertIn('BLADE 550/550MM - DREISSNER', lines[0]['product_description'])
        self.assertEqual(lines[1]['product_code'], 'C32191')
        self.assertEqual(lines[2]['quantity'], 10.0)
        self.assertIn('SUSPENSIE STABILIZATOR MOOG', lines[2]['product_description'])

    def test_invoice_ingest_shows_message_when_no_lines_extracted(self):
        job = self.env['invoice.ingest.job'].create({
            'name': 'OCR Header Only',
            'source': 'ocr',
            'state': 'needs_review',
            'attachment_id': self.env['ir.attachment'].create({
                'name': 'header_only.pdf',
                'datas': base64.b64encode(b'%PDF-1.4\n% header only\n'),
                'res_model': 'invoice.ingest.job',
                'type': 'binary',
                'mimetype': 'application/pdf',
            }).id,
        })

        self.assertFalse(job.line_ids)
        self.assertIn('No invoice lines were extracted', job.line_extraction_message or '')

    def test_invoice_ingest_exposes_duplicate_warning_fields(self):
        original = self.env['invoice.ingest.job'].create({
            'name': 'Original OCR Invoice',
            'source': 'ocr',
            'state': 'needs_review',
        })
        duplicate = self.env['invoice.ingest.job'].create({
            'name': 'Duplicate OCR Invoice',
            'source': 'ocr',
            'state': 'needs_review',
            'payload_json': json.dumps({
                'openai': {
                    'duplicate_of': original.id,
                },
            }),
        })

        self.assertEqual(duplicate.duplicate_of_job_id, original)
        self.assertIn(original.display_name, duplicate.duplicate_warning_message or '')

    def test_invoice_ingest_line_allows_manual_barcode_and_internal_code_overrides(self):
        job = self.env['invoice.ingest.job'].create({
            'name': 'OCR Override Fields',
            'source': 'ocr',
            'state': 'needs_review',
            'partner_id': self.supplier.id,
        })
        line = self.env['invoice.ingest.job.line'].create({
            'job_id': job.id,
            'product_id': self.product.id,
            'product_code': 'AUTO-TEST-001',
            'product_description': 'Test product',
            'quantity': 1.0,
            'unit_price': 10.0,
            'vat_rate': 19.0,
        })

        self.assertEqual(line.matched_internal_code, self.product.default_code)
        self.assertEqual(line.label_barcode_value, self.product.barcode)

        line.write({
            'matched_internal_code': 'MANUAL-INT-001',
            'label_barcode_value': '9876543210000',
        })

        self.assertEqual(line.matched_internal_code, 'MANUAL-INT-001')
        self.assertEqual(line.label_barcode_value, '9876543210000')
        self.assertEqual(line.manual_internal_code, 'MANUAL-INT-001')
        self.assertEqual(line.manual_barcode_value, '9876543210000')
        self.assertEqual(self.product.default_code, 'AUTO-TEST-001')
        self.assertEqual(self.product.barcode, '5941234567890')

    def test_progressive_trim_is_disabled_for_non_auto_total_supplier(self):
        job = self.env['invoice.ingest.job'].create({
            'name': 'Normal Supplier OCR',
            'source': 'ocr',
            'partner_id': self.supplier.id,
        })

        normalized = job._normalize_payload_line({
            'product_code_raw': 'C2W029ABE',
            'product_code': 'C2W029ABE',
            'product_description': 'Set placute frana',
        }, supplier=self.supplier)

        self.assertEqual(normalized['product_code'], 'C2W029ABE')

    def test_auto_total_supplier_trims_visible_product_code(self):
        job = self.env['invoice.ingest.job'].create({
            'name': 'Auto Total OCR',
            'source': 'ocr',
            'partner_id': self.auto_total_supplier.id,
        })

        normalized = job._normalize_payload_line({
            'product_code_raw': 'C2W029ABE',
            'product_code': 'C2W029ABE',
            'product_description': 'Set placute frana',
        }, supplier=self.auto_total_supplier)

        self.assertEqual(normalized['product_code'], 'C2W029')

    def test_auto_total_supplier_still_matches_on_trimmed_fallback(self):
        product = self.env['product.product'].create({
            'name': 'Auto Total Match Candidate',
            'default_code': 'C2W029',
        })
        job = self.env['invoice.ingest.job'].create({
            'name': 'Auto Total Match',
            'source': 'ocr',
            'partner_id': self.auto_total_supplier.id,
        })

        matched_product, meta = job._match_product_with_meta(
            'C2W029ABE',
            supplier=self.auto_total_supplier,
            product_description='Set placute frana',
        )

        self.assertEqual(matched_product, product)
        self.assertEqual(meta.get('method'), 'progressive_trim:default_code')

    def test_non_auto_total_supplier_never_removes_spaced_suffix_letters(self):
        job = self.env['invoice.ingest.job'].create({
            'name': 'Spaced Suffix OCR',
            'source': 'ocr',
            'partner_id': self.supplier.id,
        })

        normalized = job._normalize_payload_line({
            'product_code_raw': 'C2W029 ABE',
            'product_code': 'C2W029 ABE',
            'product_description': 'Set placute frana',
        }, supplier=self.supplier)

        self.assertEqual(normalized['product_code'], 'C2W029 ABE')

    def test_merge_fallback_line_codes_prefers_fuller_parser_code(self):
        job = self.env['invoice.ingest.job'].create({
            'name': 'Fallback Merge OCR',
            'source': 'ocr',
            'partner_id': self.supplier.id,
        })

        merged_lines, recovered_count = job._merge_fallback_line_codes(
            [{
                'product_code_raw': 'C2W029',
                'product_code': 'C2W029',
                'product_description': 'Set placute frana',
            }],
            [{
                'product_code_raw': 'C2W029ABE',
                'product_code': 'C2W029ABE',
                'supplier_brand': 'ABE',
                'product_description': 'Set placute frana',
            }],
        )

        self.assertEqual(recovered_count, 1)
        self.assertEqual(merged_lines[0].get('product_code'), 'C2W029ABE')
        self.assertEqual(merged_lines[0].get('product_code_raw'), 'C2W029ABE')
        self.assertEqual(merged_lines[0].get('supplier_brand'), 'ABE')

    def test_invoice_ingest_line_opens_tecdoc_wizard_with_defaults(self):
        job = self.env['invoice.ingest.job'].create({
            'name': 'TecDoc Match Job',
            'source': 'ocr',
            'partner_id': self.supplier.id,
        })
        line = self.env['invoice.ingest.job.line'].create({
            'job_id': job.id,
            'sequence': 10,
            'product_code': 'A9W045MT',
            'product_code_raw': 'A9W045MT',
            'product_description': 'Kit protectie praf amortizor',
        })

        action = line.action_open_tecdoc_match()

        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(action['res_model'], 'tecdoc.sync.wizard')
        wizard = self.env['tecdoc.sync.wizard'].browse(action['res_id'])
        self.assertEqual(wizard.lookup_type, 'article_no')
        self.assertEqual(wizard.article_number, 'A9W045MT')
        self.assertEqual(wizard.invoice_ingest_line_id, line)

    def test_tecdoc_wizard_can_apply_synced_product_to_invoice_line(self):
        job = self.env['invoice.ingest.job'].create({
            'name': 'TecDoc Apply Job',
            'source': 'ocr',
            'partner_id': self.supplier.id,
        })
        line = self.env['invoice.ingest.job.line'].create({
            'job_id': job.id,
            'sequence': 20,
            'product_code': 'AUTO-TEST-001',
            'product_code_raw': 'AUTO-TEST-001',
            'product_description': 'Test Automotive Product',
        })
        wizard = self.env['tecdoc.sync.wizard'].create({
            'lookup_type': 'article_no',
            'article_number': 'AUTO-TEST-001',
            'invoice_ingest_line_id': line.id,
        })

        action = wizard._apply_to_invoice_ingest_line(self.product)
        line.invalidate_recordset()

        self.assertEqual(action['type'], 'ir.actions.client')
        self.assertEqual(line.product_id, self.product)
        self.assertEqual(line.match_method, 'exact:tecdoc_sync')
        self.assertEqual(line.match_confidence, 100.0)

    def test_tecdoc_sync_prefers_post_article_number_details_and_populates_variant_data(self):
        api = self.env['tecdoc.api'].create({
            'name': 'TecDoc Test API',
            'api_key': 'test-key',
            'lang_id': 21,
            'country_filter_id': 63,
        })

        captured = []
        payload = {
            'articleNo': 'C2W029ABE',
            'countArticles': 1,
            'articles': [
                {
                    'articleId': 6183880,
                    'articleNo': 'C2W029ABE',
                    'articleProductName': 'set placute frana,frana disc',
                    'supplierName': 'ABE',
                    'supplierId': 4426,
                    'articleMediaType': 'JPEG',
                    'articleMediaFileName': 'abe.webp',
                    's3image': 'https://example.com/abe.webp',
                    'allSpecifications': [
                        {'criteriaName': 'Partea de montare', 'criteriaValue': 'HA'},
                    ],
                    'eanNo': {'eanNumbers': '5900427194311'},
                    'oemNo': [
                        {'oemBrand': 'VW', 'oemDisplayNo': '2K5698451'},
                    ],
                    'compatibleCars': [
                        {
                            'vehicleId': 756,
                            'modelId': 5431,
                            'manufacturerName': 'SEAT',
                            'modelName': 'LEON (1P1)',
                            'typeEngineName': '1.6 TDI',
                            'constructionIntervalStart': '2010-11-01',
                            'constructionIntervalEnd': '2012-12-01',
                        },
                    ],
                },
            ],
        }

        api_model = type(api)
        original_make_request = api_model._make_request

        def fake_make_request(self, endpoint, params=None, method='GET', json_data=None, form_data=None):
            captured.append({
                'endpoint': endpoint,
                'method': method,
                'params': params,
                'json_data': json_data,
                'form_data': dict(form_data or {}),
            })
            return payload

        api_model._make_request = fake_make_request
        try:
            product = api.sync_product_from_tecdoc(article_no='C2W029ABE')
        finally:
            api_model._make_request = original_make_request

        template = product.product_tmpl_id if product._name == 'product.product' else product
        self.assertTrue(captured, 'TecDoc sync should hit the API.')
        self.assertEqual(captured[0]['endpoint'], '/articles/article-number-details')
        self.assertEqual(captured[0]['method'], 'POST')
        self.assertEqual(captured[0]['form_data']['articleNo'], 'C2W029ABE')
        self.assertEqual(captured[0]['form_data']['langId'], 21)
        self.assertEqual(captured[0]['form_data']['countryFilterId'], 63)
        self.assertEqual(template.tecdoc_article_no, 'C2W029ABE')
        self.assertEqual(template.tecdoc_supplier_name, 'ABE')
        self.assertEqual(template.tecdoc_ean, '5900427194311')
        self.assertIn('VW: 2K5698451', template.tecdoc_oem_numbers or '')
        self.assertIn('Partea de montare: HA', template.tecdoc_specifications or '')
        self.assertTrue(template.tecdoc_variant_ids)
        self.assertEqual(template.tecdoc_variant_ids[:1].supplier_external_id, 4426)
        self.assertEqual(template.tecdoc_variant_ids[:1].ean_ids[:1].ean, '5900427194311')
        self.assertTrue(template.tecdoc_variant_ids[:1].vehicle_ids)

    def test_invoice_ingest_does_not_auto_sync_tecdoc_when_local_match_misses(self):
        api = self.env['tecdoc.api'].create({
            'name': 'TecDoc Test API Auto Match',
            'api_key': 'test-key',
            'lang_id': 21,
            'country_filter_id': 63,
        })
        job = self.env['invoice.ingest.job'].create({
            'name': 'Auto TecDoc OCR',
            'source': 'ocr',
            'partner_id': self.supplier.id,
        })

        api_model = type(api)
        original_sync = api_model.sync_product_from_tecdoc

        def fake_sync_product_from_tecdoc(self, article_id=None, article_no=None, supplier_id=None):
            raise AssertionError('invoice ingest should not auto-sync TecDoc during OCR matching')

        api_model.sync_product_from_tecdoc = fake_sync_product_from_tecdoc
        try:
            normalized = job._normalize_payload_line({
                'product_code_raw': 'C2W029ABE',
                'product_code': 'C2W029ABE',
                'supplier_brand': 'ABE',
                'product_description': 'Set placute frana',
            }, supplier=self.supplier)
        finally:
            api_model.sync_product_from_tecdoc = original_sync

        self.assertFalse(normalized['matched_product_id'])
        self.assertFalse(normalized['match_method'])
        self.assertEqual(normalized['supplier_brand'], 'ABE')

    def test_tecdoc_sync_does_not_create_product_for_explicit_empty_article_response(self):
        api = self.env['tecdoc.api'].create({
            'name': 'TecDoc Empty Response API',
            'api_key': 'test-key',
            'lang_id': 21,
            'country_filter_id': 63,
        })

        payload = {
            'articleNo': 'ATAS2102',
            'countArticles': None,
            'articles': None,
        }

        api_model = type(api)
        original_make_request = api_model._make_request

        def fake_make_request(self, endpoint, params=None, method='GET', json_data=None, form_data=None):
            return payload

        existing_templates = self.env['product.template'].search_count([
            ('tecdoc_article_no', '=', 'ATAS2102'),
        ])

        api_model._make_request = fake_make_request
        try:
            with self.assertRaises(UserError):
                api.sync_product_from_tecdoc(article_no='ATAS2102')
        finally:
            api_model._make_request = original_make_request

        self.assertEqual(
            self.env['product.template'].search_count([('tecdoc_article_no', '=', 'ATAS2102')]),
            existing_templates,
        )

    def test_invoice_ingest_auto_tecdoc_match_skips_explicit_empty_article_response(self):
        api = self.env['tecdoc.api'].create({
            'name': 'TecDoc Empty Response Auto Match',
            'api_key': 'test-key',
            'lang_id': 21,
            'country_filter_id': 63,
        })
        job = self.env['invoice.ingest.job'].create({
            'name': 'Auto TecDoc OCR Empty Response',
            'source': 'ocr',
            'partner_id': self.supplier.id,
        })

        api_model = type(api)
        original_sync = api_model.sync_product_from_tecdoc

        def fake_sync_product_from_tecdoc(self, article_id=None, article_no=None, supplier_id=None):
            raise UserError('Article not found in TecDoc. Verify the article number/ID and your Language/Country Filter IDs.')

        existing_templates = self.env['product.template'].search_count([
            ('tecdoc_article_no', '=', 'ATAS2102'),
        ])

        api_model.sync_product_from_tecdoc = fake_sync_product_from_tecdoc
        try:
            normalized = job._normalize_payload_line({
                'product_code_raw': 'ATAS2102',
                'product_code': 'ATAS2102',
                'supplier_brand': 'UNKNOWN',
                'product_description': 'Senzor presiune ulei',
            }, supplier=self.supplier)
        finally:
            api_model.sync_product_from_tecdoc = original_sync

        self.assertFalse(normalized['matched_product_id'])
        self.assertFalse(normalized.get('match_method'))
        self.assertEqual(
            self.env['product.template'].search_count([('tecdoc_article_no', '=', 'ATAS2102')]),
            existing_templates,
        )

    def test_openai_prompt_preserves_full_code_for_normal_suppliers(self):
        job = self.env['invoice.ingest.job'].create({
            'name': 'Prompt Normal Supplier',
            'source': 'ocr',
        })

        prompt = job._build_openai_extraction_prompt('INTER CARS ROMANIA SRL')

        self.assertIn('product_code_raw must preserve the exact printed article code', prompt)
        self.assertIn('do not remove trailing letters or suffixes', prompt)
        self.assertIn('If the printed code looks like C2W029ABE', prompt)
        self.assertNotIn('Special case for Auto Total invoices', prompt)

    def test_openai_prompt_keeps_full_code_for_auto_total_suppliers(self):
        job = self.env['invoice.ingest.job'].create({
            'name': 'Prompt Auto Total',
            'source': 'ocr',
        })

        prompt = job._build_openai_extraction_prompt('S.C. AD AUTO TOTAL S.R.L.')

        self.assertIn('Special case for Auto Total invoices', prompt)

    def test_reprocess_existing_ocr_job_requeues_same_record(self):
        attachment = self.env['ir.attachment'].create({
            'name': 'reprocess.pdf',
            'datas': base64.b64encode(b'%PDF-1.4\n% reprocess\n'),
            'res_model': 'invoice.ingest.job',
            'type': 'binary',
            'mimetype': 'application/pdf',
        })
        job = self.env['invoice.ingest.job'].create({
            'name': 'OCR Existing',
            'source': 'ocr',
            'state': 'needs_review',
            'attachment_id': attachment.id,
            'external_id': 'existing-checksum',
        })

        action = job.action_reprocess()

        self.assertEqual(action['type'], 'ir.actions.client')
        self.assertEqual(job.state, 'pending')
        self.assertTrue(job.attachment_data)
        self.assertEqual(job.attachment_filename, 'reprocess.pdf')
        async_job = self.env['automotive.async.job'].search(
            [
                ('target_model', '=', 'invoice.ingest.job'),
                ('target_method', '=', '_process_ingest_job'),
                ('target_res_id', '=', job.id),
            ],
            order='id desc',
            limit=1,
        )
        self.assertTrue(async_job)
        self.assertEqual(async_job.state, 'queued')

    def test_action_create_draft_vendor_bill_marks_job_done_when_receipt_is_validated(self):
        job = self.env['invoice.ingest.job'].create({
            'name': 'OCR Bill To Done',
            'source': 'ocr',
            'state': 'needs_review',
            'partner_id': self.supplier.id,
            'invoice_number': 'INV-DONE-001',
            'invoice_date': '2026-04-01',
            'amount_total': 100.0,
        })

        job_model = type(job)
        original_auto_receipt = job_model._auto_create_or_update_receipt

        def fake_auto_receipt(self, supplier):
            return {
                'created': True,
                'updated_lines': 1,
                'validated': True,
                'unmatched_count': 0,
            }

        job_model._auto_create_or_update_receipt = fake_auto_receipt
        try:
            action = job.action_create_draft_vendor_bill()
        finally:
            job_model._auto_create_or_update_receipt = original_auto_receipt

        self.assertEqual(action['type'], 'ir.actions.client')
        self.assertTrue(job.account_move_id)
        self.assertEqual(job.state, 'done')

    def test_action_sync_receipt_stock_marks_job_done_when_receipt_is_validated(self):
        job = self.env['invoice.ingest.job'].create({
            'name': 'OCR Sync To Done',
            'source': 'ocr',
            'state': 'needs_review',
            'partner_id': self.supplier.id,
            'invoice_number': 'INV-DONE-002',
            'invoice_date': '2026-04-01',
            'amount_total': 100.0,
        })

        job_model = type(job)
        original_auto_receipt = job_model._auto_create_or_update_receipt

        def fake_auto_receipt(self, supplier):
            return {
                'created': False,
                'updated_lines': 1,
                'validated': True,
                'unmatched_count': 0,
            }

        job_model._auto_create_or_update_receipt = fake_auto_receipt
        try:
            action = job.action_sync_receipt_stock()
        finally:
            job_model._auto_create_or_update_receipt = original_auto_receipt

        self.assertEqual(action['type'], 'ir.actions.client')
        self.assertTrue(job.account_move_id)
        self.assertEqual(job.state, 'done')

    def test_label_print_wizard_queue_mode_creates_async_job(self):
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('automotive.label_direct_print_enabled', 'true')
        icp.set_param('automotive.label_printer_name', 'Test Label Printer')

        label_payload = [self.product._prepare_label_payload()]
        wizard = self.env['automotive.label.print.wizard'].create({
            'source_model': self.product._name,
            'source_res_id': self.product.id,
            'source_display_name': self.product.display_name,
            'label_payload_json': json.dumps(label_payload, ensure_ascii=False),
            'label_count': 2,
            'copies': 1,
            'output_mode': 'queue_print',
            'printer_name': 'Test Label Printer',
            'job_name': 'Test queued labels',
        })

        action = wizard.action_process()

        self.assertEqual(action['type'], 'ir.actions.client')
        self.assertEqual(action['tag'], 'display_notification')

        async_job = self.env['automotive.async.job'].search(
            [
                ('target_model', '=', 'ir.actions.report'),
                ('target_method', '=', '_run_automotive_async_label_job'),
                ('source_model', '=', self.product._name),
                ('source_res_id', '=', self.product.id),
            ],
            order='id desc',
            limit=1,
        )
        self.assertTrue(async_job, 'Queue printing labels should enqueue a background job.')
        self.assertEqual(async_job.state, 'queued')
        payload = json.loads(async_job.payload_json or '{}')
        self.assertEqual(payload.get('printer_name'), 'Test Label Printer')
        self.assertEqual(len(payload.get('labels') or []), 1)
        self.assertEqual(payload.get('repeat_count'), 2)

    def test_label_pdf_duplication_repeats_single_rendered_page(self):
        report = self.env.ref('automotive_parts.action_report_automotive_label')
        writer = PdfFileWriter()
        writer.addBlankPage(width=72, height=72)
        base_stream = io.BytesIO()
        writer.write(base_stream)

        duplicated = report._duplicate_pdf_pages(base_stream.getvalue(), 3)

        reader = PdfFileReader(io.BytesIO(duplicated), strict=False)
        self.assertEqual(reader.getNumPages(), 3)

    def test_label_pdf_render_supports_empty_report_recordset(self):
        report = self.env.ref('automotive_parts.action_report_automotive_label')
        writer = PdfFileWriter()
        writer.addBlankPage(width=72, height=72)
        base_stream = io.BytesIO()
        writer.write(base_stream)

        with patch(
            'odoo.addons.base.models.ir_actions_report.IrActionsReport._render_qweb_pdf',
            return_value=(base_stream.getvalue(), 'pdf'),
        ):
            pdf_content, report_type = self.env['ir.actions.report']._render_qweb_pdf(
                report.report_name,
                [],
                data={'repeat_count': 2},
            )

        self.assertEqual(report_type, 'pdf')
        reader = PdfFileReader(io.BytesIO(pdf_content), strict=False)
        self.assertEqual(reader.getNumPages(), 2)

    def test_incoming_picking_create_skips_duplicate_nir_sequence_value(self):
        supplier = self.env['res.partner'].create({
            'name': 'NIR Sequence Supplier',
            'supplier_rank': 1,
        })
        picking_type = self.env.ref('stock.picking_type_in')
        existing = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'partner_id': supplier.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
            'supplier_invoice_number': 'SEQ-EXISTING',
            'nir_number': 'NIR/00009',
        })
        self.assertEqual(existing.nir_number, 'NIR/00009')

        with patch.object(type(self.env['ir.sequence']), 'next_by_code', side_effect=['NIR/00009', 'NIR/00010']):
            created = self.env['stock.picking'].with_context(skip_audit_log=True).create({
                'picking_type_id': picking_type.id,
                'partner_id': supplier.id,
                'location_id': picking_type.default_location_src_id.id,
                'location_dest_id': picking_type.default_location_dest_id.id,
                'supplier_invoice_number': 'SEQ-NEW',
            })

        self.assertEqual(created.nir_number, 'NIR/00010')
