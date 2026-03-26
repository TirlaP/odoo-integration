# 11) ANAF e-Factura integration (primary invoice source)

Goal: ingest structured invoices from ANAF (avoid OCR when possible).

## 11.1 Requirements decisions (blockers)

- [x] Scope for now: supplier-side invoice ingest for purchasing/NIR flow.
- [ ] Later decision: add customer-side e-Factura sync/export flows.

## 11.2 Must-have behaviors

- [x] OAuth2 auth flow + token refresh implemented in `anaf.efactura`.
- [x] Download messages for date ranges.
- [x] Parse UBL (XML), extract:
  - supplier/customer identifiers
  - invoice number, date, totals, VAT
  - invoice line items
- [x] Deduplicate:
  - do not import the same invoice twice
- [x] Ingest queue model (`invoice.ingest.job`) with `pending/running/needs_review/done/failed`.
- [ ] Matching UI hardening:
  - resolve “unknown supplier/product” cases
  - link vendor bill to receipt (NIR)
- [ ] End-to-end UAT with real ANAF test/prod credentials.

Acceptance:
- Importing the same ANAF invoice twice does nothing (idempotent).
- Imported invoice matches accounting totals and VAT.
