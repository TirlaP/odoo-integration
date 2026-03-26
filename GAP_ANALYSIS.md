# Gap Analysis vs Client Requirements (PDF)
Project: `custom_addons/automotive_parts` (Odoo 18)
Date: 2026-02-07

Legend:
- ✅ Implemented
- 🟡 Partially implemented / placeholder
- ❌ Not implemented

## What changed since the last review (high-signal)
- ✅ Added **TecDoc Fast** local catalog tables + UI + importer runs: `custom_addons/automotive_parts/models/tecdoc_fast_models.py`, `custom_addons/automotive_parts/models/tecdoc_fast_import.py`, `custom_addons/automotive_parts/views/tecdoc_fast_views.xml`.
- ✅ Added **Fast Import cron** runner: `custom_addons/automotive_parts/data/tecdoc_fast_import_cron.xml`.
- ✅ Added **Fast purge** wizard: `custom_addons/automotive_parts/models/tecdoc_fast_purge.py`.
- ✅ Added Node export pipeline improvements (per-supplier split + safer xref fetching): `scripts/tecdoc_split_by_supplier.js`, `scripts/tecdoc_fetch_xrefs_for_found.js`.
- ✅ Updated order readiness logic to use real stock move signals (`qty_reserved` + upstream `qty_received`) and auto-state transitions: `custom_addons/automotive_parts/models/sale_order.py`.
- ✅ Hardened NIR/invoice reception flow with indexed supplier invoice fields and rounded quantity-difference checks: `custom_addons/automotive_parts/models/stock_picking.py`.
- ✅ Reworked ANAF ingest to parse UBL payloads and create idempotent ingest jobs + vendor bill dedupe: `custom_addons/automotive_parts/models/anaf_efactura.py`, `custom_addons/automotive_parts/models/invoice_ingest.py`.

## 2.1 Management comenzi și clienți

### 2.1.1 Modul Clienți
**Operations**
- ✅ Add/Edit client (standard Odoo + `res.partner` extensions)
- ✅ List/Search/Filter (standard Odoo)
- ✅ Deactivate (logical delete): standard `active` archive exists in Odoo, but not explicitly surfaced/validated by module
- ✅ Delete rules: deletion is blocked if the customer has associated sale orders (archive instead)

**Fields**
- ✅ Tip client + CUI/CNP + validations: `custom_addons/automotive_parts/models/res_partner.py`
- 🟡 Sold curent (computed): currently computed from posted invoice residual only (does not include returns / order-level allocations)
- 🟡 Status active/inactive: standard Odoo field, not explicitly integrated in custom views
- ✅ Create date + basic audit fields (creator/modifier names): `custom_addons/automotive_parts/models/res_partner.py`, `custom_addons/automotive_parts/views/res_partner_views.xml`
- 🟡 Istoric modificări: partial via `automotive.audit.log` entries (create/write/unlink) with JSON snapshots

**Rules**
- ❌ Balance formula (orders + payments + returns): not implemented (currently invoices residual only).
- ✅ Prevent delete when orders exist: implemented (server-side).

### 2.1.2 Modul Comenzi
**Operations**
- ✅ Create/Edit/Cancel/List: standard `sale.order` + extensions in `custom_addons/automotive_parts/models/sale_order.py`
- ✅ “Edit only in allowed states”: enforced server-side (blocked after `ready_prep`)

**Order structure**
- ✅ Tip comandă, responsabil, observații: implemented as fields
- ✅ Stări comandă: `auto_state` now computed from reservation/receipt/delivery signals.
- 🟡 Position structure: `qty_reserved` / `qty_received` are now tied to stock moves (including upstream receipts); needs UAT validation on complex routes.

**Rules**
- ✅ “Editable only until Gata de pregătire”: enforced (blocked after `ready_prep`)
- ✅ Auto state updates: integrated with reservations, upstream receptions, and deliveries.
- 🟡 Audit log: create/write are logged with old/new snapshots; still missing stock lifecycle + documents coverage

## 2.2 Management produse și stocuri

### 2.2.1 Modul Produse
- ✅ Add/Edit/Deactivate/List: standard Odoo product flows
- 🟡 Search by TecDoc compatibility:
  - ✅ Added a “TecDoc Lookup” search helper (article/OEM/EAN/cross exact match) via TecDoc Fast tables
  - ❌ No rich “filter by vehicle attributes” product search UI yet
- ✅ Fields: TecDoc fields + supplier code + barcode_internal + supplier: `custom_addons/automotive_parts/models/product_product.py`
- 🟡 Purchase price / VAT handling: relies on standard Odoo fields; not explicitly handled in module requirements

**Label printing**
- 🟡 Placeholder only (`action_generate_label` displays notification). No printer integration, no “per invoice quantities”.

### 2.2.2 Modul Stocuri
- ✅ Real-time stock: provided by Odoo
- ✅ Reserved stock:
  - Product-level reserved/available KPIs use Odoo’s `outgoing_qty` (reasonable)
  - Order-line reserved/received quantities are sourced from stock moves/origin moves.
- 🟡 Views per location/status: not implemented in custom UI (but available in standard inventory reporting)

### 2.2.3 Management ciclul de viață produs
- ❌ End-to-end traceability flow Supplier → Reception → Stock → Order → Delivery → Return not implemented as a cohesive module feature.
- 🟡 Odoo supports the documents; module needs to connect and enforce traceability + audit.

## 2.3 Recepție marfă de la furnizori
- 🟡 Create reception + supplier mandatory: standard Odoo supports; module adds NIR fields on pickings.
- ✅ Barcode scanning updates move-line quantities and supports “create product if missing”.
- 🟡 ANAF integration: OAuth2 auth-code + refresh flow is implemented, plus UBL parsing and idempotent ingest/vendor bill dedupe; still needs production UAT and stronger matching UX for edge cases.
- 🟡 OCR/AI fallback: OpenAI-based PDF extraction is implemented in `invoice.ingest.job` with editable extracted lines; confidence governance and advanced review ergonomics still need hardening.
- ✅ Differences are signaled from demanded vs processed move quantity with UoM rounding.

## 2.4 Integrare TecDOC
- 🟡 Live API integration exists (`tecdoc.api`) for ad-hoc sync.
- 🟡 Local/periodic sync:
  - ✅ Added **TecDoc Fast** local catalog storage in PostgreSQL (vehicles/specs/OEM/cross) via JSON imports.
  - 🟡 Still relies on RapidAPI exports to build the local dataset (no official TecDoc dataset sync pipeline).
  - 🟡 “Annual update” is currently manual (re-export + re-import + purge strategy).
- 🟡 “TecDOC source of truth for compatibilities”: partially (compatibilities stored and searchable), but products without TecDoc are not explicitly flagged in UI and overrides are not tracked.

## 2.5 Flux inteligent de procesare comenzi
- ✅ “Ready” calc: integrated with reservation, upstream reception, and delivery signals.
- ❌ Notifications (app/email) when ready: not implemented.

## 2.6 Management plăți
- ❌ Payment recording/allocation per order/line: not implemented (should likely leverage Odoo Accounting + reconciliation).
- 🟡 Balances in real-time: partially via invoice residual only.

## 2.7 Portal mecanici
- ❌ No portal controllers/views; only “mechanic” client_type exists.

## 2.8 Audit Log
- 🟡 Audit model + UI exists (`automotive.audit.log`), but:
  - not automatic for all actions/entities,
  - capture is implemented for `res.partner` and `sale.order` create/write (and partner unlink),
  - not covering product edits, stock pickings/moves, and TecDoc fast import/purge runs.

## 2.9 Integrare contabilitate – SAGA
- ❌ Not implemented.

## 2.10 Documente și operațiuni comerciale
- 🟡 Odoo supports invoices/receipts/delivery docs; module does not add “auto-generate + archive” policy layer.
- ❌ Avize/chitanțe/internal docs + electronic archiving not implemented.

## 2.11 Stoc furnizori + comenzi la furnizori
- ❌ Not implemented (requires supplier APIs + purchase integration).

## Key Architectural Notes (recommended)
- Prefer standard Odoo objects as the “source of truth”:
  - reservations via stock moves/quants,
  - received quantities via stock move lines “done” quantities,
  - payments via accounting entries + reconciliation.
- Keep custom fields for UI/automation, but avoid duplicating core stock/accounting logic.
