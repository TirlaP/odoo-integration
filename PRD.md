# Product Requirements Document (PRD)
## Automotive Parts Management System with TecDoc Integration

**Version:** 1.1
**Date:** February 7, 2026
**Status:** In Development
**Author:** Development Team

---

## Executive Summary

This document outlines the requirements for an automotive parts distribution ERP system built on Odoo 18, designed specifically for the Romanian automotive aftermarket. The system integrates with TecDoc (via RapidAPI) for parts data, ANAF e-Factura for invoice management, and includes custom workflows for automotive parts distribution.

### Architecture Decisions (2026-02-07)
- Odoo remains the source of truth for stock, NIR, and accounting.
- ANAF direct integration is the primary path (OAuth2 + UBL ingest).
- PDF fallback is handled in Odoo via OpenAI extraction + review queue.
- SAGA integration remains optional and will be added only if operationally required.

### Key Objectives
- Streamline automotive parts distribution operations
- Integrate real-time TecDoc catalog data
- Comply with Romanian business regulations (CUI, CNP, ANAF)
- Automate stock management and order fulfillment
- Provide complete audit trail for compliance
- Enable mechanic portal for B2B customers

---

## 1. Project Overview

### 1.1 Business Context

**Industry:** Automotive Parts Distribution
**Market:** Romanian automotive aftermarket
**Business Model:** B2B and B2C distribution of automotive parts

**Current Challenges:**
- Manual parts catalog management
- Lack of vehicle compatibility information
- No integration with TecDoc standard
- Manual invoice processing from ANAF
- Complex stock management across multiple orders
- Limited visibility into order status
- No automated supplier stock checking

### 1.2 Solution Overview

An Odoo-based ERP system that:
- Integrates TecDoc for 10M+ automotive parts database
- Automates ANAF e-Factura invoice import
- Manages custom automotive workflows
- Tracks stock with automatic reservations
- Provides mechanic portal for self-service
- Generates comprehensive audit logs

---

## 2. Goals and Objectives

### 2.1 Business Goals
1. **Reduce order processing time by 60%** through automation
2. **Increase inventory accuracy to 99%+** through real-time tracking
3. **Eliminate manual data entry** for parts specifications
4. **Achieve 100% ANAF compliance** through automated e-Factura integration
5. **Improve customer satisfaction** through faster, more accurate order fulfillment

### 2.2 Technical Goals
1. Full integration with TecDoc RapidAPI
2. Real-time stock visibility and reservation
3. Automated order state management
4. Complete audit trail for all transactions
5. Mobile-friendly interface for warehouse operations

---

## 3. Target Users

### 3.1 User Personas

#### Persona 1: Warehouse Manager (Ana, 35)
**Goals:**
- Efficiently receive and process incoming goods
- Minimize stock discrepancies
- Quick product lookup and labeling

**Pain Points:**
- Manual NIR (reception) documentation
- Difficulty identifying products
- Time-consuming label printing

**How System Helps:**
- Barcode scanning for quick reception
- Automatic NIR number generation
- ANAF invoice linking
- One-click label printing

#### Persona 2: Sales Manager (Mihai, 42)
**Goals:**
- Quick order processing
- Accurate delivery estimates
- Customer balance tracking

**Pain Points:**
- Manual stock checking
- Unclear order status
- Delayed customer updates

**How System Helps:**
- Real-time stock visibility
- Automatic order state updates
- Customer balance dashboard
- Mechanic portal for self-service

#### Persona 3: Mechanic/Workshop Owner (Ion, 38)
**Goals:**
- Find correct parts for specific vehicles
- Check part availability
- Track order status

**Pain Points:**
- Uncertainty about part compatibility
- Unknown delivery times
- Manual order placement

**How System Helps:**
- TecDoc vehicle compatibility search
- Portal access for order tracking
- Real-time stock availability
- Self-service ordering

#### Persona 4: Purchasing Manager (Elena, 31)
**Goals:**
- Track supplier orders
- Match invoices to receipts
- Monitor stock levels

**Pain Points:**
- Manual invoice matching
- ANAF e-Factura complexity
- Supplier data scattered

**How System Helps:**
- ANAF e-Factura automation
- Automatic invoice-receipt matching
- Supplier performance tracking

---

## 4. Functional Requirements

### 4.1 Customer Management (Module: CRM)

#### FR-CRM-001: Customer Types
**Priority:** P0 (Critical)

**Description:**
System must support three customer types with Romanian-specific validation:

1. **Persoană Fizică (Individual)**
   - Required field: CNP (Cod Numeric Personal)
   - Validation: Exactly 13 digits
   - Auto-validation algorithm for CNP format

2. **Persoană Juridică (Company)**
   - Required field: CUI (Cod Unic de Înregistrare)
   - Validation: 2-10 digits, optional "RO" prefix
   - Integration with ANAF CUI validation API (future)

3. **Mecanic (Mechanic)**
   - Grants portal access
   - Special pricing rules
   - Self-service ordering capabilities

**Acceptance Criteria:**
- ✅ Customer creation form includes client type selector
- ✅ CUI/CNP fields show/hide based on client type
- ✅ Validation errors displayed for invalid CUI/CNP
- ✅ Cannot save customer without required identification

#### FR-CRM-002: Customer Balance Tracking
**Priority:** P0 (Critical)

**Description:**
Real-time customer balance calculation:
- Formula: Balance = Total Invoices - Total Payments + Returns
- Updated on: Invoice posting, payment registration, return processing
- Display: Customer form, customer list, reports

**Acceptance Criteria:**
- ✅ Balance updates within 1 second of transaction
- ✅ Balance visible on customer card
- ✅ Negative balances (credit) clearly indicated
- ✅ Historical balance tracking

#### FR-CRM-003: Customer Deactivation
**Priority:** P1 (High)

**Description:**
Customers with active orders cannot be deleted, only deactivated.

**Acceptance Criteria:**
- ✅ Delete button hidden for customers with orders
- ✅ Deactivate option available
- ✅ Deactivated customers excluded from default searches
- ✅ Warning message explains why deletion is blocked

---

### 4.2 Product Management (Module: Inventory + TecDoc)

#### FR-PRD-001: TecDoc Integration
**Priority:** P0 (Critical)

**Description:**
Full integration with TecDoc catalog via RapidAPI:

**Search Capabilities:**
- Search by article number
- Search by OEM number
- Search by vehicle (make/model/year)
- VIN decoding

**Data Synchronization:**
- Product name and description
- Technical specifications
- Vehicle compatibility list
- Product images/media
- Supplier information
- Cross-references

**Acceptance Criteria:**
- ✅ Search returns results within 2 seconds
- ✅ Product sync creates/updates Odoo product
- ✅ Compatibility data displayed on product form
- ✅ API errors handled gracefully with user notifications
- ✅ Rate limiting respected (per RapidAPI plan)

#### FR-PRD-002: Vehicle Compatibility
**Priority:** P0 (Critical)

**Description:**
Display and search vehicle compatibility:
- List of compatible vehicles on product page
- Search products by vehicle
- Filter by year range
- Display manufacturer, model, engine type

**Acceptance Criteria:**
- ✅ Compatibility data fetched from TecDoc
- ✅ Displayed in readable format (not raw JSON)
- ✅ Searchable by vehicle attributes
- ✅ Updates when product synced

#### FR-PRD-003: Stock Management
**Priority:** P0 (Critical)

**Description:**
Real-time stock tracking with reservations:

**Stock Types:**
- **Total Stock:** Physical quantity on hand
- **Available Stock:** Total - Reserved
- **Reserved Stock:** Committed to active orders

**Rules:**
- Stock reserved when order confirmed
- Stock released when order cancelled
- Stock decremented when order shipped
- Low stock warnings at configurable threshold

**Acceptance Criteria:**
- ✅ Stock updates in real-time
- ✅ Reservation prevents overselling
- ✅ Stock status visible on product and order
- ✅ Manual stock adjustments logged

#### FR-PRD-004: Product Labeling
**Priority:** P1 (High)

**Description:**
Generate product labels for warehouse use:
- Print on goods reception
- Print on demand from product page
- Include: Product name, barcode, location, date
- Support multiple label formats

**Acceptance Criteria:**
- ✅ Label generation triggered from NIR
- ✅ Labels include all required info
- ✅ Print button on product page
- ✅ Batch printing for multiple products

---

### 4.3 Order Management (Module: Sales)

#### FR-ORD-001: Custom Order Workflow
**Priority:** P0 (Critical)

**Description:**
Automotive-specific order states with automatic transitions:

**Order States:**
1. **Draft** - Initial creation
2. **În așteptare aprovizionare** - Waiting for stock
3. **Parțial recepționată** - Partially in stock
4. **Complet recepționată** - Fully in stock
5. **Gata de pregătire** - Ready to prepare/ship
6. **Livrată** - Delivered to customer
7. **Anulată** - Cancelled

**Auto-Transitions:**
- Draft → Waiting Supply (on confirmation)
- Waiting Supply → Partially/Fully Received (on supplier delivery)
- Fully Received → Ready to Prepare (all items in stock)
- Ready to Prepare → Delivered (on delivery confirmation)

**Acceptance Criteria:**
- ✅ States update automatically based on stock
- ✅ Manual override available for managers
- ✅ State changes logged in audit trail
- ✅ Email notifications on state change
- ✅ State visible in order list and detail views

#### FR-ORD-002: Stock Status Indicator
**Priority:** P0 (Critical)

**Description:**
Visual indicator of stock availability:
- 🔴 **None:** No items in stock
- 🟡 **Partial:** Some items in stock
- 🟢 **Full:** All items in stock

**Acceptance Criteria:**
- ✅ Color-coded badges in order list
- ✅ Updates in real-time with stock changes
- ✅ Visible without opening order
- ✅ Filterable by stock status

#### FR-ORD-003: Order Types
**Priority:** P1 (High)

**Description:**
Support two order types:
- **Internal:** Inter-warehouse transfers
- **External:** Customer orders

**Differences:**
- Internal orders don't generate invoices
- Different workflow rules
- Different reporting

**Acceptance Criteria:**
- ✅ Order type selectable on creation
- ✅ Type affects workflow and documents
- ✅ Reports segmented by type

#### FR-ORD-004: Order Line Status
**Priority:** P1 (High)

**Description:**
Track individual line item completion:
- **Incomplete:** Qty received < Qty ordered
- **Complete:** Qty received >= Qty ordered

**Acceptance Criteria:**
- ✅ Status per line visible
- ✅ Order complete when all lines complete
- ✅ Partial shipments supported

---

### 4.4 NIR - Goods Reception (Module: Inventory)

#### FR-NIR-001: NIR Number Generation
**Priority:** P0 (Critical)

**Description:**
Automatic NIR (Nota de Intrare-Recepție) number generation:
- Format: NIR/XXXXX (5 digits, sequential)
- Generated on reception creation
- Read-only, cannot be edited
- Unique per reception

**Acceptance Criteria:**
- ✅ NIR number auto-generated
- ✅ Sequential numbering maintained
- ✅ No duplicates possible
- ✅ Visible on reception form and printouts

#### FR-NIR-002: Barcode Scanning
**Priority:** P0 (Critical)

**Description:**
Barcode scanning wizard for quick reception:

**Flow:**
1. Click "Scan Barcode" on reception
2. Scan product barcode
3. System identifies product
4. Enter quantity
5. Add to reception
6. Repeat for next product

**Acceptance Criteria:**
- ✅ Wizard opens in popup
- ✅ Product auto-filled on scan
- ✅ Warning if barcode not found
- ✅ Quantity defaults to 1
- ✅ Can scan multiple products sequentially

#### FR-NIR-003: ANAF Invoice Linking
**Priority:** P1 (High)

**Description:**
Link supplier invoices from ANAF e-Factura:
- Manual invoice number entry
- Fetch from ANAF by invoice number
- Automatic invoice-receipt matching
- Quantity validation

**Acceptance Criteria:**
- ✅ Invoice number field on NIR
- ✅ "Fetch from ANAF" button
- ✅ Invoice details populated
- ✅ Discrepancies highlighted

#### FR-NIR-004: Quantity Differences
**Priority:** P1 (High)

**Description:**
Detect and highlight quantity discrepancies:
- Compare ordered vs received
- Flag differences visually
- Require manager approval for large differences
- Document reason for difference

**Acceptance Criteria:**
- ✅ Differences calculated automatically
- ✅ Visual indicator (badge/color)
- ✅ Difference report printable
- ✅ Approval workflow for >10% variance

---

### 4.5 TecDoc API Integration (Module: TecDoc)

#### FR-TEC-001: API Configuration
**Priority:** P0 (Critical)

**Description:**
Centralized TecDoc API configuration:

**Settings:**
- RapidAPI Key (encrypted storage)
- API Host URL
- Language ID (default: English/Romanian)
- Country Filter ID (default: Romania)
- Request timeout
- Rate limit settings

**Acceptance Criteria:**
- ✅ Configuration page accessible to admins
- ✅ API key stored encrypted
- ✅ Connection test button
- ✅ Usage statistics displayed
- ✅ Rate limit warnings

#### FR-TEC-002: Product Sync
**Priority:** P0 (Critical)

**Description:**
Sync wizard for importing products:

**Inputs:**
- Article number (required)
- Supplier ID (optional)

**Actions:**
1. Search TecDoc for article
2. Display search results
3. User selects product
4. Import to Odoo
5. Sync compatibility data
6. Fetch images

**Acceptance Criteria:**
- ✅ Wizard accessible from menu
- ✅ Search shows multiple results if available
- ✅ Preview before import
- ✅ Success/error notification
- ✅ Product opens after import

#### FR-TEC-003: VIN Decoder
**Priority:** P1 (High)

**Description:**
Decode vehicle VIN to identify make/model/year:
- Input: 17-character VIN
- Output: Vehicle details
- Use for: Parts search, order context

**Acceptance Criteria:**
- ✅ VIN input validates format
- ✅ Decoder returns vehicle data
- ✅ Invalid VIN shows error
- ✅ Results used for parts search

#### FR-TEC-004: Bulk Import
**Priority:** P2 (Nice to Have)

**Description:**
Import multiple products via CSV:
- Upload CSV with article numbers
- Process in background
- Email notification on completion
- Error report for failed imports

**Acceptance Criteria:**
- ✅ CSV template downloadable
- ✅ Progress bar during import
- ✅ Success/failure summary
- ✅ Detailed error log

---

### 4.6 ANAF e-Factura Integration (Module: ANAF)

#### FR-ANF-001: Configuration
**Priority:** P1 (High)

**Description:**
ANAF e-Factura API setup:
- API URL (production/test)
- OAuth2 credentials
- Company CUI
- Auto-fetch schedule

**Acceptance Criteria:**
- ✅ Configuration form available
- ✅ OAuth2 authentication flow
- ✅ Connection test successful
- ✅ Schedule configurable (hourly/daily)

#### FR-ANF-002: Invoice Fetching
**Priority:** P1 (High)

**Description:**
Automated invoice retrieval from ANAF:

**Process:**
1. Connect to ANAF API
2. Fetch invoices for last N days
3. Parse UBL/XML format
4. Match to existing suppliers (by CUI)
5. Create vendor bill in Odoo
6. Avoid duplicates

**Acceptance Criteria:**
- ✅ Scheduled job runs automatically
- ✅ UBL/XML parsing successful
- ✅ Supplier auto-matched by CUI
- ✅ No duplicate invoices created
- ✅ Errors logged and reported

#### FR-ANF-003: Manual Fetch
**Priority:** P1 (High)

**Description:**
Manual invoice processing from ingest queue:
- Trigger manual fetch/import from ANAF configuration
- Review parsed invoices in `invoice.ingest.job`
- Link created vendor bill to reception (NIR)

**Acceptance Criteria:**
- ✅ Manual fetch action available from ANAF configuration
- ✅ Parsed payload is visible in ingest job
- 🟡 Link to NIR via wizard exists; full assisted matching UX still in progress

---

### 4.7 Audit Log (Module: Audit)

#### FR-AUD-001: Automatic Logging
**Priority:** P0 (Critical)

**Description:**
Track all system changes:

**Logged Events:**
- Customer create/edit/delete
- Product create/edit/delete
- Order create/edit/cancel/deliver
- Stock movements
- NIR operations
- Price changes
- Settings changes

**Logged Data:**
- User who made change
- Timestamp
- Action type (create/write/unlink)
- Model and record ID
- Old values (where applicable)
- New values
- Description

**Acceptance Criteria:**
- ✅ All critical operations logged
- ✅ No manual logging required
- ✅ Logs immutable (read-only)
- ✅ Search and filter capabilities
- ✅ Export to CSV for audits

#### FR-AUD-002: Audit Report
**Priority:** P1 (High)

**Description:**
Generate audit reports:
- Date range selection
- Filter by user
- Filter by model
- Filter by action type
- Export to PDF/Excel

**Acceptance Criteria:**
- ✅ Report accessible from menu
- ✅ All filters functional
- ✅ Exports include all data
- ✅ Manager-only access

---

### 4.8 Mechanic Portal (Module: Portal)

#### FR-PRT-001: Self-Service Ordering
**Priority:** P2 (Future)

**Description:**
Portal for mechanic customers:
- Search products by vehicle
- View pricing
- Place orders
- Track order status
- View invoices
- See account balance

**Acceptance Criteria:**
- ✅ Mechanics can log in
- ✅ See only own data
- ✅ Place orders 24/7
- ✅ Email confirmations
- ✅ Mobile-friendly

#### FR-PRT-002: Order Tracking
**Priority:** P2 (Future)

**Description:**
Real-time order status in portal:
- View current orders
- See order state
- Est. delivery date
- Receive updates

**Acceptance Criteria:**
- ✅ Orders display current state
- ✅ Status updates in real-time
- ✅ Notifications on changes

---

## 5. Technical Requirements

### 5.1 Platform

**Odoo Version:** 18.0 Community Edition
**Python Version:** 3.12
**Database:** PostgreSQL 14+
**Operating System:** macOS, Linux (production), Windows (dev)

### 5.2 APIs

#### TecDoc API (RapidAPI)
- **Provider:** RapidAPI Hub
- **API:** TecDoc Catalog by Ron Hartman
- **Protocol:** HTTPS REST
- **Authentication:** x-rapidapi-key header
- **Rate Limits:** Per subscription plan (Basic: Free, Pro: $19/mo, Ultra: $39/mo)
- **Endpoints Used:**
  - `/articles/search/...` - Product search
  - `/articles/article-id-details/...` - Product details
  - `/articles/compatible-vehicles/...` - Compatibility
  - `/vin/decoder-v3/...` - VIN decoding
  - `/manufacturers/...` - Manufacturers
  - `/suppliers/list` - Suppliers

#### ANAF e-Factura API
- **Provider:** ANAF (Romanian Tax Authority)
- **Protocol:** HTTPS REST
- **Authentication:** OAuth 2.0
- **Data Format:** UBL/XML
- **Usage:** Invoice retrieval

### 5.3 Data Models

#### Extended Models

**res.partner (Customer/Supplier)**
```python
- client_type: Selection (individual/company/mechanic)
- cui: Char (CUI validation)
- cnp: Char (CNP validation)
- current_balance: Monetary (computed)
- is_mechanic: Boolean (computed)
```

**product.product (Product)**
```python
- tecdoc_id: Char (TecDoc Article ID)
- tecdoc_article_no: Char (Article Number)
- tecdoc_supplier_id: Integer
- tecdoc_compatibility: Text
- supplier_code: Char
- barcode_internal: Char
- stock_available: Float (computed)
- stock_reserved: Float (computed)
- main_supplier_id: Many2one('res.partner')
```

**sale.order (Sales Order)**
```python
- auto_state: Selection (custom states)
- order_type: Selection (internal/external)
- estimated_delivery_date: Date
- responsible_user_id: Many2one('res.users')
- stock_status: Selection (computed)
- observations: Text
```

**stock.picking (NIR)**
```python
- nir_number: Char (auto-generated)
- supplier_invoice_id: Many2one('account.move')
- supplier_invoice_number: Char
- supplier_invoice_date: Date
- reception_notes: Text
- received_by: Many2one('res.users')
- has_differences: Boolean (computed)
```

#### New Models

**tecdoc.api (TecDoc Configuration)**
```python
- name: Char
- api_key: Char (encrypted)
- api_host: Char
- base_url: Char
- lang_id: Integer
- country_filter_id: Integer
```

**anaf.efactura (ANAF Configuration)**
```python
- name: Char
- environment: Selection (test/prod)
- api_url: Char
- use_oauth: Boolean
- oauth_client_id / oauth_client_secret / oauth_redirect_uri
- oauth_authorization_code / oauth_state
- access_token / refresh_token / token_expires_at
- api_token: Char (legacy fallback)
- cui_company: Char
- fetch_days / fetch_filter
- last_sync_at / last_sync_message / last_fetch_count
```

**automotive.audit.log (Audit Trail)**
```python
- user_id: Many2one('res.users')
- action: Selection (create/write/unlink)
- model_name: Char
- record_id: Integer
- description: Text
- old_values: Text
- new_values: Text
- create_date: Datetime
```

### 5.4 Security

**Access Rights:**
- **Admin:** Full access to all modules
- **Manager:** All operations except system config
- **User:** Read/write own records only
- **Portal (Mechanic):** Read own orders, create orders

**Data Security:**
- API keys encrypted at rest
- Database backups daily
- Audit logs immutable
- GDPR-compliant data handling

### 5.5 Performance

**Response Times:**
- Page load: < 2 seconds
- Search: < 1 second
- TecDoc API: < 3 seconds
- Stock calculation: < 500ms

**Scalability:**
- Support 10,000+ products
- Handle 1,000+ orders/day
- 100+ concurrent users

---

## 6. User Stories

### Epic 1: TecDoc Integration

**US-1.1: Search Product by Article Number**
*As a warehouse manager, I want to search for products using TecDoc article numbers so that I can quickly identify unknown parts.*

**Acceptance Criteria:**
- Can enter article number in search field
- Results display within 2 seconds
- Product details shown clearly
- Can import product to Odoo

**US-1.2: View Vehicle Compatibility**
*As a sales rep, I want to see which vehicles a part fits so that I can confirm compatibility with customer.*

**Acceptance Criteria:**
- Compatibility list visible on product page
- Shows make, model, year
- Filterable and searchable
- Up-to-date from TecDoc

### Epic 2: Order Management

**US-2.1: Automatic Order Status**
*As a sales manager, I want orders to update their status automatically when stock arrives so that I can prioritize ready orders.*

**Acceptance Criteria:**
- Status changes without manual intervention
- Email notification on status change
- Can filter orders by status
- Status history tracked

**US-2.2: Stock Visibility**
*As a customer service rep, I want to instantly see stock status on orders so that I can give accurate delivery estimates.*

**Acceptance Criteria:**
- Stock status badge visible in order list
- Color-coded (red/yellow/green)
- Updates in real-time
- Drilling down shows line-level detail

### Epic 3: Goods Reception

**US-3.1: Quick Barcode Reception**
*As a warehouse worker, I want to scan product barcodes during reception so that I can process goods faster.*

**Acceptance Criteria:**
- Barcode scanner supported
- Product auto-identified
- Quantity entry quick
- Can scan multiple products

**US-3.2: ANAF Invoice Matching**
*As an accountant, I want to link ANAF invoices to receptions automatically so that I don't have to manually enter invoice data.*

**Acceptance Criteria:**
- Fetch invoice from ANAF
- Match to supplier by CUI
- Populate invoice fields
- Highlight discrepancies

---

## 7. Success Metrics

### 7.1 Operational KPIs

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| Order processing time | 45 min | 15 min | Time from order to shipment |
| Stock accuracy | 85% | 99% | Physical vs system count |
| Manual data entry | 80% of products | 5% of products | % products requiring manual entry |
| ANAF compliance | 60% | 100% | % invoices matched to receipts |
| Customer satisfaction | 7.2/10 | 9.0/10 | NPS score |

### 7.2 Technical KPIs

| Metric | Target | Measurement |
|--------|--------|-------------|
| System uptime | 99.5% | Monitoring |
| API response time | < 2s | Logging |
| Database query time | < 100ms | Profiling |
| Error rate | < 0.1% | Error tracking |
| Data accuracy | 99.9% | Validation checks |

---

## 8. Risks and Mitigation

### 8.1 Technical Risks

**Risk:** TecDoc API rate limiting
**Impact:** High
**Probability:** Medium
**Mitigation:**
- Implement caching layer
- Queue API requests
- Upgrade to higher RapidAPI tier if needed
- Cache common searches

**Risk:** ANAF API changes/downtime
**Impact:** High
**Probability:** Medium
**Mitigation:**
- Fallback to manual entry
- Monitor ANAF status
- Maintain invoice queue for retry
- Alert admins on failure

**Risk:** Odoo version upgrades breaking customizations
**Impact:** High
**Probability:** Low
**Mitigation:**
- Extensive testing before upgrade
- Version control all custom code
- Separate test environment
- Rollback plan

### 8.2 Business Risks

**Risk:** User adoption resistance
**Impact:** High
**Probability:** Medium
**Mitigation:**
- Comprehensive training program
- Phased rollout
- Champions in each department
- Quick wins to build confidence

**Risk:** Data migration errors
**Impact:** Critical
**Probability:** Low
**Mitigation:**
- Test migration with subset
- Validation scripts
- Parallel running period
- Backup/rollback plan

---

## 9. Implementation Timeline

### Phase 1: Foundation (Weeks 1-4) ✅ COMPLETE
- ✅ Odoo installation
- ✅ Base module development
- ✅ TecDoc API integration
- ✅ Customer management (CUI/CNP)
- ✅ Product management
- ✅ Order workflow
- ✅ NIR functionality
- ✅ Audit logging

### Phase 2: Integration & Testing (Weeks 5-8)
- ANAF e-Factura OAuth2 + UBL ingest hardening/UAT
- OpenAI PDF extraction fallback hardening/UAT
- Label printer integration
- Portal development for mechanics
- Supplier API integration (if available)
- User acceptance testing
- Performance optimization
- Security audit

### Phase 3: Deployment (Weeks 9-10)
- Data migration
- Training materials creation
- Staff training sessions
- Phased rollout
- Go-live support
- Monitoring and bug fixes

### Phase 4: Optimization (Weeks 11-12)
- Performance tuning
- User feedback implementation
- Additional training
- Documentation finalization
- Handover to support team

---

## 10. Future Enhancements

### 10.1 Short-term (3-6 months)
- Mobile app for warehouse
- Advanced reporting dashboard
- Predictive stock levels
- Customer price lists
- Promotion management
- SMS notifications

### 10.2 Long-term (6-12 months)
- AI-powered demand forecasting
- Integration with accounting software (SAGA)
- E-commerce website integration
- Multi-warehouse support
- Real-time supplier stock checking
- Advanced analytics and BI

---

## 11. Appendix

### 11.1 Glossary

- **TecDoc:** Standardized automotive parts data catalog
- **NIR:** Nota de Intrare-Recepție (Goods Reception Note)
- **CUI:** Cod Unic de Înregistrare (Unique Registration Code for companies)
- **CNP:** Cod Numeric Personal (Personal Numeric Code for individuals)
- **ANAF:** Agenția Națională de Administrare Fiscală (Romanian Tax Authority)
- **e-Factura:** Electronic invoicing system mandated by ANAF
- **OEM:** Original Equipment Manufacturer
- **VIN:** Vehicle Identification Number
- **UBL:** Universal Business Language (XML format for invoices)

### 11.2 References

- [Odoo 18 Documentation](https://www.odoo.com/documentation/18.0/)
- [TecDoc RapidAPI](https://rapidapi.com/ronhartman/api/tecdoc-catalog)
- [ANAF e-Factura](https://www.anaf.ro/efactura)
- [Romanian Business Regulations](https://legislatie.just.ro/)

---

**Document Control**

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-01-09 | Dev Team | Initial PRD |

---

**Approval Signatures**

| Role | Name | Signature | Date |
|------|------|-----------|------|
| Product Owner | | | |
| Technical Lead | | | |
| Business Stakeholder | | | |

---

*End of Product Requirements Document*
