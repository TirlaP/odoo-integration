# -*- coding: utf-8 -*-
{
    'name': 'Automotive Parts Management',
    'version': '1.0.0',
    'category': 'Sales',
    'summary': 'Complete automotive parts management with TecDoc integration',
    'description': """
        Automotive Parts Management System
        ===================================

        Features:
        ---------
        * TecDoc API Integration
        * Customer management (Individual, Company, Mechanic)
        * Romanian fields (CUI, CNP)
        * Product management with vehicle compatibility
        * Custom order workflow
        * NIR (Goods Reception) management
        * Label printing
        * ANAF e-Factura integration
        * Mechanic portal
        * Stock management with reservations
        * Payment management
        * Audit log
    """,
    'author': 'Your Company',
    'website': 'https://www.yourcompany.com',
    'depends': [
        'base',
        'sale_management',
        'sale_stock',
        'stock',
        'purchase',
        'account',
        'portal',
        'web',
    ],
    'assets': {
        'web.assets_frontend': [
            'automotive_parts/static/src/js/browser_diagnostics.js',
        ],
        'web.assets_backend': [
            'automotive_parts/static/src/js/browser_diagnostics.js',
            'automotive_parts/static/src/js/invoice_ingest_live_status.js',
            'automotive_parts/static/src/js/pdf_drop_binary_field.js',
            'automotive_parts/static/src/xml/pdf_drop_binary_field.xml',
            'automotive_parts/static/src/scss/pdf_drop_binary_field.scss',
            'automotive_parts/static/src/scss/invoice_ingest_table.scss',
        ],
    },
    'data': [
        'security/mechanic_security.xml',
        'security/commercial_document_security.xml',
        'security/ir.model.access.csv',
        'data/mail_templates.xml',
        'data/mechanic_portal_request_sequence.xml',
        'data/commercial_document_sequence.xml',
        'report/label_report.xml',
        'views/res_partner_views.xml',
        'views/product_views.xml',
        'views/res_config_settings_views.xml',
        'views/sale_order_views.xml',
        'views/stock_barcode_scan_wizard_views.xml',
        'views/stock_picking_views.xml',
        'views/anaf_invoice_wizard_views.xml',
        'views/invoice_ingest_views.xml',
        'views/tecdoc_views.xml',
        'views/tecdoc_fast_views.xml',
        'views/action_paths.xml',
        'views/menu_views.xml',
        'views/mechanic_portal_request_views.xml',
        'views/payment_views.xml',
        'views/commercial_document_views.xml',
        'data/product_data.xml',
        'data/invoice_ingest_cron.xml',
        'data/anaf_efactura_cron.xml',
        'data/tecdoc_fast_import_cron.xml',
    ],
    'demo': [],
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
