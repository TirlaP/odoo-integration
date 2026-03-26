# 02) Odoo base setup (so the ERP “makes sense”)

Odoo can do most ERP workflows out of the box, but you must configure it once.

## 2.1 Company & localization

- [ ] Set company data (Romania): name, address, VAT/CUI, registry, bank accounts.
- [ ] Set currency (RON) and language(s).
- [ ] Install/enable Romanian accounting localization if required (chart of accounts, taxes, invoice formats).

Acceptance:
- Invoices and reports use correct currency and VAT behavior.

## 2.2 Users & permissions (minimal)

- [ ] Create roles (at least):
  - Sales
  - Warehouse
  - Purchasing
  - Accounting
  - Admin
- [ ] Confirm they can access **Automotive Parts** menus but cannot change admin-only settings.

Acceptance:
- Non-admin users cannot edit sensitive configuration.

## 2.3 Warehouse configuration

- [ ] Define warehouse(s) and stock locations:
  - main warehouse
  - returns location
  - (optional) multiple shelves / bins
- [ ] Configure operation types and sequences (incoming/outgoing/internal).

Acceptance:
- Receiving creates stock on hand only after validation.
- Delivery reduces stock only after validation.

## 2.4 Accounting basics (needed for “sold curent”)

- [ ] Configure:
  - journals (sales, purchases, bank, cash)
  - payment methods
  - fiscal positions (if needed)
  - VAT taxes (19% etc)

Acceptance:
- You can create a customer invoice, register a payment, and see residuals update.

