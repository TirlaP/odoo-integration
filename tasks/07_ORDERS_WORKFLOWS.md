# 07) Orders (Sales app) — what must work end-to-end

## 7.1 Order lifecycle

- [ ] Quotation → Confirmed order
- [x] Reservation signals are real (stock moves), not “computed guesses”.
- [x] “Ready for preparation” should become true when:
  - all lines are reserved and/or received and ready to ship.

Acceptance:
- An order with stock available goes to “ready” without manual hacks.
- Partial receipts keep order in partial state until complete.

## 7.2 Custom automotive states (module)

Current state logic exists but is simplified.

- [x] Decide whether you keep the custom `auto_state` (and make it correct), or rely on standard Odoo states.
- [x] If keeping it: compute from real stock moves + receipts + deliveries.

Acceptance:
- `auto_state` matches reality across partial receptions and partial deliveries.
