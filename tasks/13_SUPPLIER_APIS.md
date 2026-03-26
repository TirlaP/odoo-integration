# 13) Supplier stock visibility + placing orders (2.11)

## 13.1 Supplier connectors

- [ ] Pick the first 1–2 suppliers (the ones that matter most).
- [ ] For each supplier, implement:
  - search by SKU/OEM/EAN
  - availability + price + lead time
  - place order (if supported)

Acceptance:
- From a product screen you can see supplier availability and create a purchase order.

## 13.2 Purchasing integration

- [ ] Map supplier API results to:
  - vendor pricelists
  - RFQ lines
  - expected delivery dates

Acceptance:
- Purchasing team can create RFQs with correct vendor lead times and prices.

