---
name: odoo-accounting-stock-guardrails
description: Use when touching Odoo accounting, payments, stock, sale-stock flows, or cross-module operational logic. Preserves standard accounting and stock truth while allowing custom business overlays.
---

# Odoo Accounting and Stock Guardrails

Use this skill when the task touches:

- `account.move`
- `account.payment`
- reconciliation
- `stock.picking`, `stock.move`, `stock.move.line`
- sale/stock/invoice linkage

## Core rule

Do not fight Odoo’s accounting or stock truth.

- Accounting truth belongs to standard journal entries, invoices, payments, and reconciliation.
- Stock truth belongs to standard pickings, moves, reservations, and done quantities.
- Custom logic should sit on top of those models, not replace them.

## Payments

- Keep `account.payment` and invoice reconciliation as the accounting source of truth.
- If the business needs order-level or line-level payment tracking, add a custom allocation layer on top.
- Do not redefine `payment_state`, residuals, or journal entry math unless the task explicitly requires deep accounting work.

## Stock and receptions

- Do not fake reservations by posting notes or changing custom fields only.
- Do not mark receipts or deliveries as operationally complete without stock moves supporting it.
- When linking receptions to sales, prefer standard move ancestry and procurement-compatible relations.

## Cross-module linkage

Use existing standard anchors first:

- order -> invoice: `sale.order.invoice_ids`
- order -> delivery: `sale.order.picking_ids`
- payment -> invoice: `invoice_ids` / `reconciled_invoice_ids`
- receipt/delivery truth: `stock.move` / `stock.move.line`

## Safety checks

- currency consistency
- company consistency
- partner commercial-entity consistency
- no hidden semantic redefinition of already-used financial fields

## If unsure

Prefer:
- additive custom model
- explicit action button
- explicit summary fields

Avoid:
- patching reconciliation internals
- bypassing stock workflows
- inventing parallel truths for totals, quantities, or payment status
