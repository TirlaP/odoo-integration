# Auto B YLY Requirements Audit

Source PDF moved from `/Users/petruinstagram/Downloads/autobyyly.pdf`.

Local copies:

- `docs/client_requirements/autobyyly.pdf`
- `docs/client_requirements/autobyyly.md`

## Executive Summary

Most backend pieces exist, but client confusion is real because flows are still exposed like Odoo modules instead of one automotive workflow.

Main missing work:

1. Make `Piese Auto` the operator workspace.
2. Remove top navbar `Import AI`; make `New` on `Importuri facturi` open upload wizard.
3. Add explicit return/credit-note stock workflow for supplier returns.
4. Build POS-like `Vinde` screen for fast customer sale/order/invoice flow.
5. Merge Audit/Runtime logs into one operational log screen.
6. Add supplier live stock/order APIs later.
7. Defer SAGA explicitly with client signoff.

## Status Matrix

| Area | Status | Evidence | Notes |
| --- | --- | --- | --- |
| Client/customer management | Mostly implemented | `res_partner.py`, `res_partner_views.xml` | Romanian fields, balance, delete guard, audit exist. Still exposed through Odoo-ish contact UX. |
| Orders | Partial/usable backend | `sale_order.py`, `sale_order_views.xml` | Auto states, stock status, edit restrictions, payment/return balance exist. Needs simpler order creation UI. |
| Products/stock | Mostly implemented | `product_product.py`, stock views | Product/search/stock pieces exist. Needs old-app style inventory workspace. |
| NIR / supplier receipt | Implemented for positive invoices | `invoice_ingest_job_receipt.py`, `stock_picking.py` | Ingest can create vendor bill and receipt, validate receipt, archive NIR. |
| Supplier return / credit note stock | Missing custom flow | `invoice_ingest_job_receipt.py:295` | Credit notes skip receipt sync. Need outgoing return picking or negative stock movement model. |
| TecDoc | Implemented/local sync oriented | `tecdoc_*`, scripts | Not same as supplier live stock. |
| Supplier stock/order APIs, section 2.11 | Missing | original PDF section 2.11, `tasks/13_SUPPLIER_APIS.md` | Needs supplier API list and credentials. |
| Payments | Partial | `automotive_payment_allocation.py`, `payment_views.xml` | Allocation exists. UX still accounting-heavy. |
| Mechanic portal | Partial | `controllers/portal.py`, mechanic views/security | Exists, but UAT needed. |
| Commercial docs, section 2.10 | Implemented as archive + standard docs | `commercial_document_archive.py`, `account_move.py`, `stock_picking.py` | Auto archive exists for posted invoices/bills and validated pickings. Custom Romanian print templates still unclear. |
| Audit log | Partial | `audit_log.py` | Business audit exists for selected flows. Not truly "all actions". |
| Runtime logs | Implemented separate | `runtime_log.py`, `runtime_logging.py` | Useful for debugging, but client wants one log menu. |
| SAGA | Missing/deferred | original PDF section 2.9 | Should stay future, but document signoff. |
| POS / Vinde | Missing business integration | no `point_of_sale` dependency | Existing POS app is separate. Need custom fast sales UI or integrated POS screen. |

## Client Confusion Diagnosis

The client expects one system like old PoligonAuto:

- left workflow menu
- create order directly
- add parts from bottom quick-entry row
- status buckets: offers, open, finalized, deleted
- sales screen with cart, cash/card, fiscal/non-fiscal
- NIR screen with import buttons at top
- inventory filters by supplier/category/manufacturer/out-of-stock

Current Odoo setup has backend glue but user sees:

- separate Odoo apps: Sales, POS, Invoicing, Purchase, Inventory
- `Piese Auto` menu still jumps into standard Odoo actions
- duplicate invoice import concepts: `Importuri facturi` and `Import AI`
- separate Audit Logs and Runtime Logs

So implementation is not the main failure. Workflow packaging is.

## Invoice Corpus Findings

`FACTURI EX` has 49 supplier PDFs.

Important fixture classes:

- normal Romanian supplier invoices
- multi-page invoices with warranty/declaration pages that must be ignored for lines
- EU/VAT 0 invoices and currency variations
- credit/return invoices with negative quantities
- credit/return invoices with trailing minus values like `832,69-`
- return invoices linked to original invoice/document references
- exchange/core return lines
- image-only PDFs: `SUA26_*.pdf`, where `pdftotext` returns empty

Return examples:

- AD Auto Total: `22602890327.pdf`, `22603123799.pdf`
- Conex: `DownloadInvoice.pdf`, `DownloadInvoice (1).pdf`
- Elit: `2600181450.pdf`, `2600190631.pdf`
- Autonet: `FSMERPX8639428_20260408_11044190.pdf`
- Inter Cars: `ROPD7526001025.pdf`, `ROPD7526001138.pdf`
- Materom trailing-minus: `926950354.pdf`, `926975808.pdf`

## Recommended Implementation Plan

### Phase 1: UX Unblock

1. Hide/remove `Import AI` menu from top navbar.
2. Override `New` on `Importuri facturi` to open upload wizard.
3. Rename invoice ingest buttons/messages to Romanian operator language.
4. Add Piese Auto menus for:
   - Comenzi
   - Vinde
   - Clienti
   - Inventar
   - NIR / Importuri facturi
   - Documente comerciale
   - Jurnal
5. Keep standard Odoo apps installed, but stop making operators use them.

### Phase 2: Return Stock Correctness

1. Treat supplier credit notes as a return operation, not only `in_refund`.
2. On matched return lines, create supplier return picking from stock to supplier, or equivalent controlled negative stock movement.
3. Preserve original invoice reference when present.
4. Add tests for:
   - negative qty return
   - positive qty plus trailing-minus value return
   - credit note with no matched product
   - duplicate return invoice

### Phase 3: Commercial Documents

1. Keep `commercial.document.archive`.
2. Add filtered actions under `Documente comerciale`:
   - Facturi furnizor
   - Facturi clienti
   - Chitante
   - Avize
   - NIR
   - Documente interne
3. Ensure ingest-created vendor bills and NIR entries are visible from archive and standard documents.
4. Decide whether client needs custom printable Romanian templates or only archive/search.

### Phase 4: Vinde / POS-Like Flow

Build a custom Odoo backend screen or POS integration that copies old workflow:

- customer selector or quick customer fields
- vehicle fields: marca, model, nr inmatriculare, serie sasiu
- bottom/add-line product search by name/barcode/code/internal code/brand/supplier
- cart/order table
- paid/unpaid
- cash/card
- fiscal/non-fiscal action
- create order/invoice/delivery from one screen

This is more valuable than exposing standard POS as-is.

### Phase 5: Supplier APIs and SAGA

Supplier APIs are section 2.11 and still missing. Do after core workflow:

1. pick first supplier with real API support
2. map product code/EAN/OEM to supplier SKU
3. show supplier stock/price/lead time
4. create RFQ/purchase order from shortage lines

SAGA should stay future unless client demands it now. Get explicit written deferral.

## Immediate Code Changes To Do Next

1. Remove `Import AI` menu item.
2. Make `New` in `Importuri facturi` open upload wizard.
3. Add return-stock flow for credit notes.
4. Create combined log menu/action.
5. Add invoice fixture tests from `FACTURI EX`.

