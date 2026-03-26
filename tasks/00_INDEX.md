# Tasks (start → finish)

This folder is a practical checklist for turning this repo into a working ERP for automotive parts:
- TecDoc catalog import (fast DB catalog)
- stock, purchasing, sales, deliveries, returns
- invoicing + payments + customer balances
- ANAF e-Factura ingestion (primary) + OpenAI PDF fallback
- audit log + reporting

Conventions:
- `[ ]` = not done, `[x]` = done
- “Acceptance” bullets are how you know a task is actually complete.

## Start here (order)

1) `tasks/01_ENVIRONMENT.md`
2) `tasks/02_ODOO_BASE_SETUP.md`
3) `tasks/04_MASTER_DATA.md`
4) `tasks/05_TECDOC_CATALOG.md`
5) `tasks/06_STOCK_WORKFLOWS.md`
6) `tasks/07_ORDERS_WORKFLOWS.md`
7) `tasks/08_RECEIVING_NIR.md`
8) `tasks/10_INVOICING_PAYMENTS.md`
9) `tasks/11_ANAF_EFACTURA.md`
10) `tasks/12_OCR_PIPELINE.md`
11) `tasks/14_AUDIT_REPORTING.md`
12) `tasks/15_GO_LIVE.md`
13) `tasks/16_ODOO_UI_GUIDE.md` (read anytime)

## Quick “what’s already in the repo”

- Custom addon: `custom_addons/automotive_parts`
- Dev helper: `./dev` (start/update/logs/open)
- TecDoc Node exporters: `scripts/tecdoc_fetch_from_xml.js`, `scripts/tecdoc_split_by_supplier.js`, `scripts/tecdoc_fetch_xrefs_for_found.js`
- TecDoc Fast import UI: **Automotive Parts → TecDoc → Fast Import**
- Invoice ingest UI: **Automotive Parts → ANAF e-Factura → Invoice Ingest Jobs**
