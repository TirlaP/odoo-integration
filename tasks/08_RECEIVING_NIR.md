# 08) Reception (NIR) + barcode scanning

## 8.1 NIR number and supplier invoice fields

- [x] Incoming pickings generate NIR number (already implemented).
- [x] Confirm supplier invoice number/date fields are filled and searchable.

Acceptance:
- Warehouse user can create a receipt and it gets an NIR number automatically.

## 8.2 Barcode scanning workflow (must be corrected)

Current wizard updates demanded qty, not done qty.

- [x] Update scanning to:
  - find product by barcode / internal barcode
  - create/update move **line** done quantity (not demand)
  - support scanning the same product multiple times (increments done qty)
- [x] If product not found:
  - allow “create product” flow (minimal required fields) or route to a resolution queue.

Acceptance:
- Scanning + validating a receipt increases stock correctly without manual editing.

## 8.3 Invoice matching (vendor bill ↔ receipt)

- [x] Decide the matching strategy:
  - ANAF e-Factura as the primary source (preferred)
  - OpenAI PDF extraction fallback for non-ANAF docs/scans
- [x] Ensure you do not duplicate invoices and you can link one vendor bill to one receipt.
- [x] Keep a review state (`needs_review`) before posting accounting documents.
- [ ] Add explicit receiving-side button flow to create ingest job directly from NIR attachment.

Acceptance:
- One supplier invoice cannot be imported twice.
- Receipt has a linked vendor bill and quantities/prices can be audited.
