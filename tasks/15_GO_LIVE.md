# 15) Go-live checklist (so it “just works”)

## 15.1 Data migration

- [ ] Import customers/vendors
- [ ] Import opening stock (initial inventory adjustment)
- [ ] Import TecDoc catalog (fast import)
- [ ] Configure prices/pricelists

Acceptance:
- You can create a real order from start to finish using real data.

## 15.2 End-to-end rehearsal (must pass)

- [ ] Create customer
- [ ] Create quotation → confirm
- [ ] Check reservation / availability
- [ ] Receive supplier goods (NIR) → validate
- [ ] Deliver customer order → validate
- [ ] Invoice customer → post
- [ ] Register payment
- [ ] Return flow (one item) → credit note

Acceptance:
- The numbers make sense (stock and accounting) and the UI matches reality.

## 15.3 Ops & monitoring

- [ ] Cron jobs verified (TecDoc fast import, ANAF ingestion, invoice ingest queue)
- [ ] Backup verified
- [ ] Basic alerting (email/log monitoring) for job failures

Acceptance:
- If a cron job fails at 2am, you can see it and recover without data loss.
