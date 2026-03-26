# 10) Invoicing & payments (Accounting app)

## 10.1 Customer invoicing

- [ ] Define invoicing policy:
  - ordered quantities vs delivered quantities
- [ ] Ensure invoice creation follows your operational flow.

Acceptance:
- You can create an invoice from a sales order and it matches delivered quantities (if that’s your policy).

## 10.2 Payments and allocation

- [ ] Decide “Sold curent” calculation:
  - recommended: based on Accounting residuals (invoices - payments - credit notes)
- [ ] Configure:
  - payment journals
  - bank/cash registers
  - payment terms

Acceptance:
- Registering a payment reduces invoice residual and customer balance updates.

## 10.3 Returns / credit notes

- [ ] Configure credit note workflow tied to returns.

Acceptance:
- A return generates a credit note and reduces the customer balance.

