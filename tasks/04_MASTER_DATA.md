# 04) Master data you must set up

## 4.1 Customers (Clienți)

- [ ] Decide “source of truth” for customer balance:
  - recommended: Accounting (invoices + payments + credit notes)
- [ ] Validate fields:
  - `client_type`, `cui`, `cnp` (already implemented)
- [ ] Train users on archive vs delete:
  - delete is blocked if orders exist (implemented)

Acceptance:
- Sales team can create customers with valid CUI/CNP and cannot delete customers with orders.

## 4.2 Vendors (Furnizori)

- [ ] Create vendor records, payment terms, delivery lead times.
- [ ] Decide “main supplier” usage:
  - you can store a preferred vendor per product, but TecDoc has multiple suppliers.

Acceptance:
- RFQs can be created and sent to vendors.

## 4.3 Products (how to use Odoo fields)

For each product you actually sell/stock:
- [ ] Product Type = Goods (storable)
- [ ] Track Inventory = enabled
- [ ] Internal Reference (`default_code`) = your internal SKU (often the TecDoc article no)
- [ ] Barcode = scan code (EAN) if you have it
- [ ] Sales price and taxes
- [ ] Cost method strategy (standard cost / AVCO) as per accounting requirements

Acceptance:
- You can receive stock for a product, then sell it, then deliver it.

## 4.4 Pricelists & pricing rules

- [ ] Decide:
  - single price per product, or
  - customer pricelists (B2B vs B2C, mechanics, etc.)
- [ ] Define margin rules (if needed):
  - based on purchase cost
  - based on vendor + lead time

Acceptance:
- Quotation uses correct price automatically.

