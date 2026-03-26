# 12) Invoice OCR / AI extraction (fallback)

Use OCR only when you cannot get structured data (e.g., scanned PDFs, non-ANAF documents).

## 12.1 Recommended approach (practical)

- Primary: ANAF e-Factura (UBL) ingestion (structured, reliable)
- Fallback: OpenAI extraction into a “draft” invoice + review UI

## 12.2 Implementation direction chosen (2026-02-07)

- [x] First fallback provider: OpenAI API (`gpt-4o-mini` by default).
- [x] Extraction action implemented on `invoice.ingest.job` (`action_extract_with_openai`).
- [x] Parsed output includes header + invoice lines + confidence and product match status.
- [x] Draft vendor bill creation from parsed lines.
- [x] Add dedicated line-level review table UI (editable extracted lines + product remap).
- [x] Supplier-aware product matching improved:
  - code normalization (spaces/hyphens/OCR noise)
  - match by supplier code / internal code / TecDoc article / barcodes
  - fallback by product description when code is missing or noisy
- [ ] Add quick remap shortcuts/actions (bulk remap, smart suggestions).
- [ ] Add optional second provider (only if OpenAI quality/cost is insufficient).

## 12.3 Optional vendor alternatives (if needed)

When choosing, compare:
- accuracy on your suppliers’ PDFs
- Romanian language/diacritics behavior
- line-item extraction quality (the hard part)
- cost per page / per document
- latency + rate limiting
- data residency/privacy requirements

Common “invoice extraction” products:
- Azure AI Document Intelligence (prebuilt invoice model)
- AWS Textract (AnalyzeExpense)
- Google Cloud Document AI (Invoice Parser)
- ABBYY (commercial OCR/extraction)
- Open-source OCR (Tesseract) + LLM post-processing (more engineering, less reliable)

Starter links (official docs):
- Azure AI Document Intelligence (invoice): https://learn.microsoft.com/azure/ai-services/document-intelligence/prebuilt/invoice?view=doc-intel-4.0.0
- AWS Textract (AnalyzeExpense): https://docs.aws.amazon.com/textract/latest/dg/analyzing-expense.html
- Google Document AI processors (Invoice Parser): https://cloud.google.com/document-ai/docs/processors-list
- ABBYY FineReader Engine lifecycle note: https://support.abbyy.com/hc/en-us/articles/360021620219-ABBYY-FineReader-Engine-end-of-life

## 12.3.1 What’s “best” in practice (recommended decision)

Do not pick based on marketing. Do this instead:
- [ ] Collect 30–50 real invoices from your top suppliers (PDFs + scans).
- [ ] Run a bake-off with 2 providers (and optionally 1 open-source baseline):
  - Azure invoice model vs AWS AnalyzeExpense is usually the fastest comparison
- [ ] Score:
  - header fields (supplier, invoice no, date, totals, VAT)
  - line items (qty, unit price, VAT rate, SKU text)
  - robustness on bad scans
  - cost and throughput

Recommendation for Romania specifically:
- Primary: ANAF e-Factura (UBL) for compliant electronic invoices (structured).
- OCR fallback: keep OpenAI first; add Azure/AWS only if benchmark data proves a clear win.

## 12.4 Implementation tasks (in Odoo)

- [x] Store invoice PDFs as `ir.attachment`.
- [x] Use `invoice.ingest.job`:
  - state: pending/running/needs_review/done/failed
  - source: anaf/ocr/manual
  - link to created `account.move` (draft) or to an exception queue
- [x] Add review UI to fix:
  - supplier mismatch
  - product mapping
  - VAT/totals discrepancies
- [ ] Improve review UI ergonomics (bulk operations, confidence-based highlighting).
- [ ] After approval, post the vendor bill and link to receipt/NIR automatically when possible.

Acceptance:
- A user can upload a PDF and end up with a draft vendor bill with extracted totals + lines, and a “needs review” warning when confidence is low.
