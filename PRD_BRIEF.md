# Product Requirements Document (Brief)
# Automotive Parts Management (Odoo 18) + TecDoc + Romanian Ops

**Doc purpose:** Concise, code-aligned PRD for the `custom_addons/automotive_parts` module in this repo.  
**Related:** `PRD.md` (long-form / aspirational), `README.md` (setup + usage).

## 1) Summary
Build an Odoo 18 application for Romanian automotive parts distribution that adds:
- TecDoc-powered product enrichment (RapidAPI)
- Romanian customer identifiers (CUI/CNP) + basic validation
- Order visibility for stock availability
- NIR-style receiving helpers (NIR number, reception notes, barcode-assisted line creation)
- ANAF e-Factura direct integration (OAuth2 + UBL ingest)
- OpenAI-based PDF extraction fallback for supplier invoices
- Admin menus for TecDoc config, ANAF config, and an Audit Log

## 2) Goals
- Reduce time to identify the correct part (TecDoc sync + compatibility notes).
- Reduce receiving friction for incoming shipments (NIR number, barcode scan wizard).
- Improve visibility into “can we deliver?” (stock available vs reserved shown on products and on orders).
- Provide a basic compliance trail (audit log entries for key actions).

## 3) Non-goals (current scope)
- Full mechanic self-service portal (only “mechanic” flag exists today; portal flows are not implemented).
- Full ANAF outbound flows (customer invoice upload, status orchestration and advanced reconciliation).
- Advanced reservation/ATP logic (current implementation relies on Odoo’s `outgoing_qty` and simplified order-line computations).

## 4) Users
- **Sales user:** creates orders, checks stock readiness, sees customer balance.
- **Warehouse user:** receives goods, scans barcodes, prints labels (placeholder), notes discrepancies.
- **Admin:** configures TecDoc/ANAF settings, reviews audit logs.

## 5) Functional Requirements (aligned to current code)

### 5.1 Customers (res.partner)
- **Client Type:** `individual` / `company` / `mechanic`.
- **Romanian IDs:** store `cui` and `cnp`.
- **Validation:** basic format checks when `client_type` matches (digits/length).
- **Balance:** computed monetary `current_balance` from posted invoices’ residual amounts.
- **Audit trail fields:** show creator/modifier names (`create_uid_name`, `write_uid_name`).
- **Actions:** button to open related sales orders.

### 5.2 Products (product.product)
- **TecDoc fields:** `tecdoc_id`, `tecdoc_article_no`, `tecdoc_supplier_id`, `tecdoc_compatibility`.
- **Automotive flags:** `is_automotive_part`, `barcode_internal`, `supplier_code`, `main_supplier_id`.
- **Stock KPIs:** `stock_available = qty_available - outgoing_qty`, `stock_reserved = outgoing_qty`.
- **Actions:**
  - “Sync from TecDoc” (requires `tecdoc_id` + configured API record).
  - “View compatible vehicles” (not a relational view; shows a notification message).
  - “Generate label” (placeholder notification).

### 5.3 Sales Orders (sale.order + sale.order.line)
- **Order metadata:** `order_type`, `estimated_delivery_date`, `responsible_user_id`, `observations`.
- **Stock status:** `stock_status` computed from order lines vs product `stock_available`.
- **Automotive state:** `auto_state` computed/updated based on `stock_status` (simplified).
- **Line KPIs:** `qty_reserved` (simplified) and `line_state` based on `qty_received`.

### 5.4 Receiving (stock.picking)
- **NIR number:** generated for incoming pickings via sequence `stock.picking.nir`.
- **Invoice info:** supplier invoice fields + optional link wizard.
- **Differences indicator:** `has_differences` compares demanded qty vs done qty (via move lines).
- **Barcode scan wizard:** `stock.barcode.scan.wizard` finds product by `barcode` or `barcode_internal` and creates/updates a `stock.move` line (simplified).

### 5.5 TecDoc Integration
- **Configuration model:** `tecdoc.api` stores RapidAPI key/host/base URL and defaults for language/country filter.
- **HTTP integration:** simple `requests.get` wrapper with error-to-UserError mapping.
- **Sync wizard:** `tecdoc.sync.wizard` searches by article number, syncs first result into an Odoo product.

### 5.6 ANAF e-Factura (implemented foundation)
- **Configuration model:** `anaf.efactura` stores environment/CUI + OAuth2 settings and access/refresh tokens.
- **OAuth flow:** authorize URL action, authorization code exchange, token refresh.
- **Fetch flow:** `listaMesajeFactura` + `descarcare` download, ZIP/XML processing, UBL parsing, idempotent ingest jobs.
- **Vendor bill dedupe:** repeated ingest does not create duplicate documents.

### 5.7 PDF AI extraction fallback (OpenAI)
- **Ingest model:** `invoice.ingest.job` supports `manual/anaf/ocr` sources and review states.
- **PDF extraction action:** “Extract with OpenAI” reads PDF attachment text and extracts structured JSON.
- **Line matching:** extracted product codes are matched to local products; unmatched lines are flagged for review.
- **Draft bill creation:** builds vendor bill lines from extracted payload (with fallback line if parsing is incomplete).

### 5.8 Audit Log
- **Model:** `automotive.audit.log` for create/write/custom events.
- **Current logging:** customer and order create/write operations create audit entries (others can be added later).
- **Views/menus:** list + form views, read-only for normal users, editable for system admins.

## 6) UX / Navigation
- Top-level menu: **Automotive Parts**
  - TecDoc: Configuration, Sync Products
  - ANAF e-Factura: Configuration
  - Audit Log

## 7) Security / Compliance Requirements
- Do not store secrets in source control or demo docs; API keys should be user-provided in Odoo UI (or via env/secret management).
- Access control:
  - Users can read audit logs; only admins can edit.
  - Users can manage TecDoc/ANAF config records (currently granted to `base.group_user`).

## 8) Open Gaps / Next Iteration Candidates
- Mechanic portal flows (portal users, ordering, tracking, pricing rules).
- Real receiving quantities (integrate with Odoo’s standard “done quantities” flow instead of editing demanded qty).
- Proper reservations per order line (linking to stock moves/quant reservations).
- TecDoc: support multiple search results selection and supplier filters; caching/rate limit handling.
- ANAF: signature validation and robust reconciliation to receptions in all edge cases.
- OpenAI extraction: add richer line-level review UX (remap product/supplier inline).

## 9) Acceptance Criteria (high-level)
- Module installs on Odoo 18 without view parse errors.
- A user can configure TecDoc credentials and sync a product from an article number.
- Stock KPIs display on product form and update when stock/reservations change.
- Incoming picking creates an NIR number and barcode wizard can add product moves.
- Audit log entries are created for partner/order create/write operations.
