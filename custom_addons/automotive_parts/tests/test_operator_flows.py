# -*- coding: utf-8 -*-
import base64
import json

from odoo.tests.common import TransactionCase, tagged


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

    def test_same_uploaded_file_creates_new_ocr_jobs(self):
        payload = base64.b64encode(b'%PDF-1.4\n% identical content\n')
        wizard_one = self.env['invoice.ingest.upload.wizard'].create({
            'supplier_id': self.supplier.id,
            'pdf_file': payload,
            'pdf_filename': 'same_file.pdf',
        })
        action_one = wizard_one.action_import_document()
        first_job = self.env['invoice.ingest.job'].browse(action_one['res_id'])

        wizard_two = self.env['invoice.ingest.upload.wizard'].create({
            'supplier_id': self.supplier.id,
            'pdf_file': payload,
            'pdf_filename': 'same_file_again.pdf',
        })
        action_two = wizard_two.action_import_document()
        second_job = self.env['invoice.ingest.job'].browse(action_two['res_id'])

        self.assertNotEqual(first_job.id, second_job.id)
        self.assertNotEqual(first_job.external_id, second_job.external_id)

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

    def test_progressive_trim_stays_enabled_for_auto_total_supplier(self):
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

    def test_openai_prompt_preserves_full_code_for_normal_suppliers(self):
        job = self.env['invoice.ingest.job'].create({
            'name': 'Prompt Normal Supplier',
            'source': 'ocr',
        })

        prompt = job._build_openai_extraction_prompt('INTER CARS ROMANIA SRL')

        self.assertIn('product_code_raw must preserve the exact printed article code', prompt)
        self.assertIn('do not remove trailing letters or suffixes', prompt)
        self.assertNotIn('Special case for Auto Total invoices', prompt)

    def test_openai_prompt_allows_auto_total_special_case(self):
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

    def test_create_test_copy_creates_new_ocr_job_on_local(self):
        self.env['ir.config_parameter'].sudo().set_param('web.base.url', 'http://localhost:8069')
        attachment = self.env['ir.attachment'].create({
            'name': 'duplicate.pdf',
            'datas': base64.b64encode(b'%PDF-1.4\n% duplicate\n'),
            'res_model': 'invoice.ingest.job',
            'type': 'binary',
            'mimetype': 'application/pdf',
        })
        job = self.env['invoice.ingest.job'].create({
            'name': 'OCR Existing',
            'source': 'ocr',
            'state': 'needs_review',
            'attachment_id': attachment.id,
            'external_id': 'same-checksum',
            'partner_id': self.supplier.id,
        })

        action = job.action_create_test_copy()

        self.assertEqual(action['type'], 'ir.actions.act_window')
        new_job = self.env['invoice.ingest.job'].browse(action['res_id'])
        self.assertTrue(new_job.exists())
        self.assertNotEqual(new_job.id, job.id)
        self.assertEqual(new_job.attachment_id, job.attachment_id)
        self.assertEqual(new_job.state, 'pending')
        self.assertNotEqual(new_job.external_id, job.external_id)
        async_job = self.env['automotive.async.job'].search(
            [
                ('target_model', '=', 'invoice.ingest.job'),
                ('target_method', '=', '_process_ingest_job'),
                ('target_res_id', '=', new_job.id),
            ],
            order='id desc',
            limit=1,
        )
        self.assertTrue(async_job)

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
        self.assertEqual(len(payload.get('labels') or []), 2)
