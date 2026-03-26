# 06) Stock workflows (Inventory app) — what must work

## 6.1 Receiving (incoming)

- [ ] Receive goods from vendor:
  - create receipt
  - set done quantities
  - validate receipt
- [ ] Confirm stock increases only after validation.

Acceptance:
- Stock on hand changes correctly after a receipt is validated.

## 6.2 Delivery (outgoing)

- [ ] Confirm a sales order creates a delivery.
- [ ] Reserve products (standard Odoo reservation).
- [ ] Validate delivery → stock decreases.

Acceptance:
- Deliveries reflect reserved vs done quantities.

## 6.3 Returns

- [ ] Configure returns flow (customer returns + vendor returns).
- [ ] Ensure returns create correct stock moves + (optionally) credit notes.

Acceptance:
- Returned quantities re-enter stock and accounting reflects the return.

