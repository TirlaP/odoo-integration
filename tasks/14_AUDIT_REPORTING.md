# 14) Audit log + reporting

## 14.1 Audit log coverage

Current audit log exists but coverage is incomplete.

- [ ] Decide what must be logged (minimum):
  - customers
  - orders
  - product critical fields (price, barcode, supplier)
  - receipts + deliveries validation
  - invoice creation/posting
  - TecDoc fast purge/import runs

Acceptance:
- For a real-life flow (receive → sell → invoice → pay), you can trace who changed what and when.

## 14.2 Reports you’ll actually use

- [ ] Stock valuation & aging
- [ ] Top sellers
- [ ] Out of stock / reorder
- [ ] Customer balances & overdue
- [ ] Vendor performance (lead time, backorders)

Acceptance:
- At least one dashboard per role (Sales, Warehouse, Purchasing, Accounting).

