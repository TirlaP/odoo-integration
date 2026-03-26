# Product Backlog (from Client PDF)
Project: `custom_addons/automotive_parts` (Odoo 18)
Date: 2026-01-09

This backlog is organized by epics mapped to sections 2.1–2.11 of the client requirements.

Legend:
- P0 = must-have for MVP
- P1 = should-have after MVP
- P2 = later / optional

## Phase 0 — Foundations (P0)
**Goal:** make the current module safe, consistent with Odoo 18 conventions, and ready to evolve.

### EP-0: Hygiene, security, conventions
- P0: Remove secrets from docs and enforce config via UI/env (RapidAPI keys, tokens).
- P0: Normalize Odoo 18 view conventions (`<list>`, no `attrs/states`) across the module.
- P0: Convert `create()` overrides to `@api.model_create_multi` where appropriate and fix recursion patterns.
- P0: Add basic logging/monitoring guidance (`README.md`) and “how to update module” steps.

### EP-1: Audit Log baseline
- P0: Define a consistent audit payload schema (JSON old/new values, operation, model, record id, user, timestamp).
- P0: Ensure audit entries are created for core entities touched by the module:
  - customers, orders, products, receptions/pickings (at minimum create/write/cancel/validate).

## Phase 1 — Core Operations (P0)
**Goal:** meet 2.1–2.3 at an MVP level using standard Odoo flows.

### EP-2 (2.1.1): Clients
- P0: Enforce “cannot delete if orders exist”; support archive/unarchive as the official “logical delete”.
- P0: Add “Status activ/inactiv” visibility in the UI (optional, if not already visible).
- P0: Expand “Sold curent” spec alignment:
  - Decision: compute from Accounting (invoices/payments/credit notes) or from Sales Orders + payment allocations.
  - Implement whichever the client accepts; default recommendation is Accounting-based.
- P1: Add returns into balance (credit notes/returns).

### EP-3 (2.1.2): Orders + workflow
- P0: Enforce edit restrictions: order editable only until `auto_state == ready_prep` (allow cancel).
- P0: Ensure automatic order readiness uses real reservation/receipt signals (not only `outgoing_qty`).
- P0: Line-level fields:
  - `qty_reserved` sourced from reservation/moves,
  - `qty_received` sourced from receipts/pickings (move lines done qty),
  - `line_state` computed accordingly.
- P1: Notifications when order becomes ready (in-app + optional email).

### EP-4 (2.2.2): Stock reservation correctness
- P0: On sale order confirmation: reserve stock using standard Odoo reservation mechanisms.
- P0: On order cancel: release reservations (standard Odoo should handle; verify).
- P0: Stock KPIs shown in UI (available/reserved) based on standard fields.

### EP-5 (2.3): Supplier receiving (NIR)
- P0: Reception must:
  - require supplier,
  - create/update done quantities correctly,
  - update stock only on validate (standard Odoo).
- P0: Barcode scanning flow:
  - scan → identify product (barcode or internal barcode),
  - if not found: offer “create product” flow,
  - add to picking as done quantity (move line) rather than editing demand.
- P0: Quantity differences:
  - compare demanded vs done and highlight before validation.
- P1: Attach invoice documents to receptions and store a structured link.

## Phase 2 — Integrations (P1)

### EP-6 (2.4): TecDoc
- P0/P1 decision: **RapidAPI live** vs **local TecDoc dataset**.
  - If local required: introduce a TecDoc sync pipeline (periodic sync, annual update process).
  - Keep live RapidAPI for MVP if permitted, then add local mode later.
- P1: Compatibility as source of truth:
  - mark products “No TecDoc” explicitly,
  - prevent editing of TecDoc-derived compatibility manually (or track overrides).
- P1: Search UX:
  - add search filters by TecDoc article, vehicle, compatibility.

### EP-7 (2.3): ANAF e-Factura / OCR
- P1: ✅ Implemented foundation: ANAF OAuth2 auth/refresh, message download, UBL parsing, idempotent ingest.
- P1: ✅ Implemented foundation: invoice dedupe on ingest/vendor bill creation.
- P1: ✅ Implemented: stronger invoice-line product matching by supplier + normalized code fields + description fallback.
- P1: In progress: faster manual resolution UX (bulk remap/suggestions).
- P1: ✅ Implemented foundation: OpenAI PDF extraction fallback for non-ANAF docs.
- P2: Optional second extraction provider only if benchmark shows clear benefit.

### EP-8 (2.11): Supplier stock & purchasing APIs
- P1: Supplier connectors (per supplier API):
  - fetch availability, price, lead time,
  - place purchase orders.
- P2: Multi-supplier sourcing suggestions and automation.

## Phase 3 — Finance, Portal, External Accounting (P2 unless mandated)

### EP-9 (2.6): Payments
- P1/P2: Decide accounting approach:
  - use Odoo invoices + payments + reconciliation (recommended),
  - or implement custom payment allocation per order/line.
- P2: Real-time balance dashboards for customers + mechanics.

### EP-10 (2.7): Mechanic portal
- P2: Separate mechanic authentication (portal users).
- P2: Portal pages:
  - active orders, history, balances, payments, documents,
  - request/ticket flow.

### EP-11 (2.9): SAGA (Cantitativ-Valoric) integration
- P2: Define interchange format and transport (CSV/XML/API).
- P2: Export receptions/deliveries/returns and reconcile.
- P2: Bidirectional sync + discrepancy alerts.

### EP-12 (2.10): Documents & archiving
- P1/P2: Automated document generation (NIR, invoices, delivery notes, receipts) with archiving policy.
- P2: Electronic archiving (attachments, retention, metadata, search).

## Decisions Required (blockers)
1) TecDoc: is “local dataset + annual update” mandatory for MVP?
2) Accounting: should “Sold curent” follow invoices/payments/credit notes (recommended) or sales orders + custom payment allocations?
3) Label printing: printer model + label format + integration method (ZPL, driver, print server).
4) ANAF: direct integration mandatory vs allowed OCR workaround.
5) Supplier APIs: which suppliers first + API access details.
