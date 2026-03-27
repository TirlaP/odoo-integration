# Implementation Gaps Assessment

## Scope

This document maps the current implementation in this repository against the client requirement set.

Environment reviewed:

- Odoo version: `18.0`
- Deployment model: self-hosted / on-premise
- Main custom addon: `custom_addons/automotive_parts`
- Relevant standard Odoo localization present in repo: `l10n_ro_edi`

Important distinction:

- This repository contains real custom automotive functionality.
- Some requirement coverage comes from standard Odoo, not from custom automotive logic.
- Several requirements are only partially covered.
- Some statements previously made about live database state or operational setup are not verifiable from source review alone and should not be treated as code-proven facts.

Verification basis:

- `Implementation_Gaps.md`
- `custom_addons/automotive_parts/__manifest__.py`
- `custom_addons/automotive_parts/models/*.py`
- `custom_addons/automotive_parts/views/*.xml`
- `custom_addons/automotive_parts/controllers/*.py`
- `custom_addons/automotive_parts/security/*`
- `odoo/addons/l10n_ro_edi/*` as needed

Not verified from source code:

- live database record counts
- current token presence/absence in a deployed database
- current company-data completeness in production
- current payment journal misconfiguration in production
- the client PDF attachment, which was not available in the workspace contents reviewed

Status legend used below:

- `Implemented`: substantially covered now, with both model logic and usable UI exposure where relevant
- `Partial`: meaningful implementation exists, but important parts are missing, weak, or rely mostly on standard Odoo
- `Standard Odoo Only`: requirement is only covered by generic Odoo behavior; no meaningful custom automotive implementation was found
- `Missing`: no meaningful implementation found in this codebase

## Executive Summary

This project is not an empty prototype. It already contains substantial custom implementation in these areas:

- customer typing and Romanian identification fields
- automotive order states and line-level stock visibility
- mechanic portal access and automotive order status display
- TecDoc API integration plus a substantial local fast-import catalog
- NIR metadata and barcode-assisted reception helpers
- PDF text extraction plus AI-assisted invoice normalization
- inbound ANAF e-Factura fetch code
- selective custom audit logging

However, the previous assessment was too optimistic in several places.

The most important corrections are:

- payments are not partially implemented as custom functionality; they are effectively standard Odoo only
- OCR is not implemented as a real OCR/image pipeline; the current flow is PDF text extraction plus AI normalization
- ANAF inbound fetch exists, but the downstream receipt/product-match automation is weaker than previously claimed
- TecDoc is strong, but not fully complete against the business requirement
- intelligent order flow exists, but depends significantly on standard Odoo reservation behavior
- several claims about operational blockers were database/runtime claims, not source-code findings

Important omitted risks:

- inbound ANAF accepts `Invoice` and `CreditNote`, but downstream bill creation always uses `in_invoice`
- duplicate prevention is source-scoped; the same supplier invoice can still exist once from OCR and once from ANAF
- inbound ANAF and outbound `l10n_ro_edi` use separate credential/configuration systems
- there are no addon tests in `custom_addons/automotive_parts`

## 2.1 Management comenzi si clienti

### 2.1.1 Modul Clienti

Status: `Partial`

What exists now:

- `res.partner` is extended with `client_type` values for individual / company / mechanic
- Romanian fields `cui` and `cnp` are implemented
- `current_balance` is exposed
- mechanic portal access is tracked and synchronized
- delete is blocked when the customer has associated sale orders
- customer form and tree views expose automotive fields
- custom audit log hooks exist on partner create / write / unlink

Evidence:

- `custom_addons/automotive_parts/models/res_partner.py`
- `custom_addons/automotive_parts/views/res_partner_views.xml`

What is missing or weaker than required:

- there is no dedicated business customer code such as `CLI-000123`; only the Odoo record ID exists
- `current_balance` is not computed as `orders - payments - returns`; it is derived from accounting receivable position (`commercial_partner.credit`)
- there is no dedicated customer history UI beyond generic audit exposure and standard metadata fields
- search and filtering are mostly standard Odoo list-view behavior, not a dedicated customer workspace
- the partner form exposes orders, but not a wired smart button for invoices in the reviewed view

Correct conclusion:

- customer master-data extension is real
- requirement coverage is partial, not fully implemented

### 2.1.2 Modul Comenzi

Status: `Partial`

What exists now:

- automotive order type: `internal` / `external`
- automotive order lifecycle states:
  - `draft`
  - `waiting_supply`
  - `partial_received`
  - `fully_received`
  - `ready_prep`
  - `delivered`
  - `cancel`
- ETA, responsible user, mechanic link, and observations fields
- line fields for reserved qty, received qty, and completion state
- automatic order-state recomputation exists
- edit restriction exists after `ready_prep`
- audit log entries exist for sale order create / write / custom transitions

Evidence:

- `custom_addons/automotive_parts/models/sale_order.py`
- `custom_addons/automotive_parts/views/sale_order_views.xml`

What is missing or weaker than required:

- the visible statusbar does not expose all modeled states; `partial_received` and `cancel` are omitted
- multi-filter listing is mostly standard Odoo list/search behavior
- "all modifications are registered in Audit Log" is overstated:
  - `sale.order` changes are logged
  - `sale.order.line` changes are not custom-audited as first-class events
- internal and external order types exist as a field, but there is no substantial divergent business logic between them

Correct conclusion:

- custom order logic exists and is meaningful
- this section should be described as `Partial`, not effectively finished

## 2.2 Management produse si stocuri

### 2.2.1 Modul Produse

Status: `Partial`

What exists now:

- product identifiers:
  - internal code
  - supplier code
  - barcode
  - internal barcode
- main supplier field
- TecDoc fields:
  - article identifiers
  - supplier metadata
  - OEM numbers
  - specifications
  - compatibility
  - media metadata
- product form and product tree exposure for automotive/TecDoc fields
- sync from TecDoc and compatible-vehicle actions

Evidence:

- `custom_addons/automotive_parts/models/product_product.py`
- `custom_addons/automotive_parts/views/product_views.xml`

What is missing or weaker than required:

- label printing is not implemented as a printer integration
- product label generation is placeholder-only notification logic
- there is no implemented "labels per invoice quantity" flow
- there is no explicit "product without TecDoc" policy/state beyond absence of TecDoc fields

Correct conclusion:

- custom product/TecDoc extension is real
- label-printer requirement is still missing

### 2.2.2 Modul Stocuri

Status: `Partial`

What exists now:

- available stock is computed as on-hand minus outgoing
- reserved stock is exposed
- stock-alert / replenishment-rule management is custom implemented
- standard Odoo stock and reservation engine is available through `sale_stock`

Evidence:

- `custom_addons/automotive_parts/models/product_product.py`
- `custom_addons/automotive_parts/views/product_views.xml`

What is missing or weaker than required:

- there is no dedicated custom stock dashboard by product / location / availability state
- reservation and release behavior are mostly standard Odoo, not custom automotive logic
- there is no explicit custom operational screen for reserved-vs-available stock flows

Correct conclusion:

- stock visibility is partly custom
- stock reservation/release should be described explicitly as mostly standard Odoo behavior

### 2.2.3 Management ciclul de viata produs

Status: `Partial`

What exists now:

- supplier reception / NIR metadata exists
- stock availability and delivery state are traceable through standard stock objects plus custom order-state logic
- custom audit hooks exist on several key objects

Evidence:

- `custom_addons/automotive_parts/models/stock_picking.py`
- `custom_addons/automotive_parts/models/sale_order.py`
- `custom_addons/automotive_parts/models/audit_log.py`

What is missing or weaker than required:

- there is no dedicated end-to-end lifecycle object or dashboard for `Supplier -> Reception -> Stock -> Order -> Delivery -> Return`
- returns are not implemented as a custom business flow
- traceability is fragmented across standard Odoo objects
- no dedicated return/reversal lifecycle was found

Correct conclusion:

- lifecycle coverage is partial

## 2.3 Receptie marfa de la furnizori

Status: `Partial`

What exists now:

- incoming pickings carry NIR number and supplier invoice metadata
- quantity differences are flagged through `has_differences`
- barcode scan wizard can identify products and create a new product if missing
- a vendor bill can be linked to a reception
- PDF ingest creates invoice jobs and can create draft vendor bills
- invoice ingest can create/update a receipt and validate it
- custom ANAF fetch can download XML payloads from SPV and create ingest jobs

Evidence:

- `custom_addons/automotive_parts/models/stock_picking.py`
- `custom_addons/automotive_parts/models/invoice_ingest.py`
- `custom_addons/automotive_parts/models/anaf_efactura.py`
- `custom_addons/automotive_parts/views/stock_picking_views.xml`
- `custom_addons/automotive_parts/views/invoice_ingest_views.xml`
- `custom_addons/automotive_parts/views/anaf_invoice_wizard_views.xml`

Important corrections:

- this is not a true OCR pipeline
  - the current flow accepts PDFs only
  - it extracts embedded text with `pdftotext` / `PyPDF2`
  - it then uses OpenAI to normalize parsed text
  - if the PDF has no usable text, the code explicitly tells the user to connect an OCR provider
- ANAF inbound is only partially wired downstream
  - ANAF creates ingest jobs from XML
  - but ANAF payload lines are not normalized into `invoice.ingest.job.line`
  - draft bill creation for ANAF can therefore fall back to a generic line
  - receipt stock sync can skip because there are no matched products
- duplicate prevention is not strong enough
  - uniqueness is scoped by `source`
  - the same supplier invoice can exist once from OCR and once from ANAF
  - there is no robust cross-source duplicate-safe reception reuse
- "reception must be linked to a supplier" is not enforced as a hard custom rule
- differences are only signaled by same-picking demand-vs-done comparison; there is no supplier invoice vs PO reconciliation engine
- ANAF fetch code exists, but no addon data file installs a cron for it

Correct conclusion:

- reception functionality is materially implemented
- this section should remain `Partial`, with stricter wording than before

## 2.4 Integrare TecDoc

Status: `Partial`

What exists now:

- live TecDoc API integration via RapidAPI
- response caching
- supplier-catalog sync
- manual sync of products from TecDoc
- local fast-import system from exported JSON directories
- local catalog models for suppliers, variants, vehicles, OEM numbers, cross numbers, and specifications
- cache inspection UI
- purge tooling for fast-imported catalog data
- TecDoc-backed product linking and search helpers

Evidence:

- `custom_addons/automotive_parts/models/tecdoc_api.py`
- `custom_addons/automotive_parts/models/tecdoc_cache.py`
- `custom_addons/automotive_parts/models/tecdoc_fast_import.py`
- `custom_addons/automotive_parts/models/tecdoc_fast_models.py`
- `custom_addons/automotive_parts/models/tecdoc_fast_purge.py`
- `custom_addons/automotive_parts/views/tecdoc_views.xml`
- `custom_addons/automotive_parts/views/tecdoc_fast_views.xml`

What is missing or weaker than required:

- no formal annual update governance or approval workflow
- no explicit catalog version tracking
- no enforced business rule that TecDoc is the locked source of truth
- no explicit marking/policy for products without TecDoc

Correct conclusion:

- TecDoc implementation is strong
- it is still safer to classify as `Partial`, not fully complete against the business brief

## 2.5 Flux inteligent de procesare comenzi

Status: `Partial`

What exists now:

- order auto-state changes based on reserved / received quantities
- ready-state activity is created
- optional ready email notification exists via configuration
- portal and form screens expose automotive status and stock readiness

Evidence:

- `custom_addons/automotive_parts/models/sale_order.py`
- `custom_addons/automotive_parts/models/res_config_settings.py`
- `custom_addons/automotive_parts/views/sale_order_views.xml`
- `custom_addons/automotive_parts/views/res_config_settings_views.xml`
- `custom_addons/automotive_parts/data/mail_templates.xml`

Important corrections:

- `_reserve_stock()` does not reserve stock; it only posts an insufficient-stock notification
- actual reservation behavior still comes mainly from standard Odoo `sale_stock`
- the notification model is limited to chatter/activity/email; there is no dedicated operational notification center
- receipt-driven readiness is weaker than it first appears because auto-created receptions are not linked to sales/procurement chains

Correct conclusion:

- custom automation exists
- this section should be downgraded from `Implemented` to `Partial`

## 2.6 Management plati

Status: `Standard Odoo Only`

What exists now:

- standard Odoo invoice and payment foundations are present through `account`
- partner current balance is visible

Evidence:

- standard Odoo accounting objects
- `custom_addons/automotive_parts/models/res_partner.py`

What is missing:

- no custom payment model
- no payment allocation per order
- no payment allocation per multiple orders
- no payment allocation per order line
- no custom linkage between payment, delivery, and automotive order lifecycle
- no mechanic portal debt / payment history module

Important correction:

- this should not be described as a partially implemented custom automotive payment module
- it is more accurate to describe it as standard Odoo only, with custom automotive payment scope still missing

## 2.7 Portal mecanici

Status: `Partial`

What exists now:

- separate mechanic portal group
- access rules limiting mechanics to their own orders and order lines
- mechanic dashboard route
- order / quotation visibility
- automotive status visibility in portal pages

Evidence:

- `custom_addons/automotive_parts/controllers/portal.py`
- `custom_addons/automotive_parts/security/mechanic_security.xml`
- `custom_addons/automotive_parts/views/sale_order_views.xml`
- `custom_addons/automotive_parts/models/res_partner.py`

What is missing or weaker than required:

- no dedicated debt / outstanding balance screen
- no portal payment history
- no dedicated request/ticket workflow
- no dedicated document hub
- security scope is limited to sale orders and sale order lines
- the dashboard renders order recordsets with `.sudo()`, which is a design risk even though the search domain is still scoped

Correct conclusion:

- assessment is broadly accurate here

## 2.8 Audit Log

Status: `Partial but meaningful`

What exists now:

- custom audit log model exists
- hooks exist for:
  - partners
  - product variants/templates
  - sale orders
  - stock pickings
  - TecDoc fast import runs
  - barcode scan custom actions
  - some order-state transitions

Evidence:

- `custom_addons/automotive_parts/models/audit_log.py`
- `custom_addons/automotive_parts/models/res_partner.py`
- `custom_addons/automotive_parts/models/product_product.py`
- `custom_addons/automotive_parts/models/sale_order.py`
- `custom_addons/automotive_parts/models/stock_picking.py`
- `custom_addons/automotive_parts/models/tecdoc_fast_import.py`

What is missing:

- this is not "all system actions"
- no comprehensive hooks were found for:
  - payments
  - invoices
  - invoice-ingest jobs
  - ANAF config changes
  - procurement
  - returns
  - sale order lines as first-class change events

Correct conclusion:

- the previous direction was correct
- the document should state more explicitly that the audit layer is selective, not comprehensive

## 2.9 Integrare contabilitate - SAGA

Status: `Missing`

What exists now:

- no SAGA-specific models, controllers, exports, imports, or sync jobs were found in `custom_addons/automotive_parts`

What is missing:

- export of receptions to SAGA
- export of deliveries to SAGA
- export of returns to SAGA
- bidirectional synchronization
- accounting vs operational stock reconciliation layer

Correct conclusion:

- this section was already assessed correctly

## 2.10 Documente si operatiuni comerciale

Status: `Partial`

What exists now:

- custom NIR numbering/metadata exists
- vendor bill linking is implemented
- customer invoices and stock delivery documents exist through standard Odoo
- attachments/electronic records exist through standard Odoo storage

Evidence:

- `custom_addons/automotive_parts/models/stock_picking.py`
- `custom_addons/automotive_parts/models/invoice_ingest.py`
- `custom_addons/automotive_parts/data/product_data.xml`

What is missing or weaker than required:

- no dedicated custom aviz module
- no dedicated custom chitanță module
- no dedicated custom internal-document framework
- no custom document archive module
- no custom report/template layer was found in this addon

Correct conclusion:

- this section is broadly correct, but mostly standard-Odoo-driven outside NIR metadata/linking

## 2.11 Vizualizarea stocului la furnizori si comenzi la furnizori

Status: `Missing`

What exists now:

- TecDoc supplier and article catalog data exists

What is missing:

- supplier live stock lookup
- supplier live price lookup
- supplier live quantity lookup
- supplier purchase-order API submission

Correct conclusion:

- this section was already assessed correctly

## ANAF / SPV Status

There are two separate Romanian e-Factura concerns in the current system:

### 1. Inbound supplier invoice fetch from SPV

Current status: `Partial`

What exists now:

- custom model for ANAF OAuth / token handling
- fetch from `listaMesajeFactura`
- download from `descarcare`
- XML/ZIP parsing
- idempotent ingest-job creation for ANAF source
- draft bill creation attempt

Evidence:

- `custom_addons/automotive_parts/models/anaf_efactura.py`
- `custom_addons/automotive_parts/models/invoice_ingest.py`

Important corrections:

- inbound fetch is real
- it is not fully wired end to end for matched-line -> receipt-sync automation
- unknown suppliers can stop in review
- no installed addon cron was found for automatic fetch scheduling
- `CreditNote` XML is accepted as inbound, but downstream bill creation still uses `move_type='in_invoice'`

Correct conclusion:

- inbound ANAF is implemented in code, but only partially operational as an automated business flow

### 2. Outbound customer invoice send to SPV

Current status: `Standard Odoo Only`

What exists now:

- standard Odoo `l10n_ro_edi` source code in the repository supports:
  - CIUS-RO XML generation
  - SPV send flow
  - SPV fetch-status / download-signature flow

Evidence:

- `odoo/addons/l10n_ro_edi/models/account_move_send.py`
- `odoo/addons/l10n_ro_edi/models/account_move.py`
- `odoo/addons/l10n_ro_edi/models/account_edi_xml_ubl_ciusro.py`
- `odoo/addons/l10n_ro_edi/views/account_move_views.xml`
- `odoo/addons/l10n_ro_edi/views/res_config_settings_views.xml`

Important corrections:

- outbound SPV sending is not custom functionality of `automotive_parts`
- `automotive_parts` does not declare `l10n_ro_edi` as a dependency
- therefore source review can confirm the capability exists in the repo, but not that it is installed/enabled in the target deployment
- specific live claims such as "currently sent from the wrong company context" are not source-code findings

### Missing risk that should be documented

- inbound custom ANAF config and outbound `l10n_ro_edi` company config are separate systems
- no bridge/unified configuration layer was found

## Hidden Implemented Features Omitted Previously

The previous document omitted some real custom features already present:

- stock-alert / replenishment-rule management for products
- TecDoc supplier-catalog sync
- TecDoc cache inspection UI
- TecDoc fast purge tooling
- configurable ready-email notifications

Evidence:

- `custom_addons/automotive_parts/models/product_product.py`
- `custom_addons/automotive_parts/models/tecdoc_api.py`
- `custom_addons/automotive_parts/models/tecdoc_cache.py`
- `custom_addons/automotive_parts/models/tecdoc_fast_purge.py`
- `custom_addons/automotive_parts/models/res_config_settings.py`

## Bottom Line

This project already contains real custom implementation across:

- customers
- orders
- mechanic portal
- TecDoc
- reception helpers
- invoice ingest
- inbound ANAF fetch
- audit logging

However, the client brief is broader than the current implementation.

The most important missing or incomplete areas are:

- payment management as custom automotive scope
- returns lifecycle
- supplier APIs
- SAGA sync
- real label-printer integration
- full OCR/image ingestion
- duplicate-safe ANAF/OCR reception flow
- production-ready, unified Romanian e-Factura operational setup

## Recommended Corrections to Team Messaging

The team can confidently say:

- there is meaningful custom automotive functionality already built
- TecDoc support is substantial
- reception/NIR helpers are real
- mechanic portal order visibility is real
- inbound ANAF fetch code exists

The team should not claim as finished:

- payment management
- returns
- supplier API integrations
- SAGA integration
- label-printer support
- full OCR
- fully automated ANAF-to-stock flow
- comprehensive audit logging
- outbound Romanian e-Factura as custom automotive functionality
