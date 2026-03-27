---
name: odoo-repo-orchestrator
description: Use when coordinating larger Odoo work across multiple features or agents. Helps sequence independent slices, define ownership, and keep accounting/stock/security-sensitive files from colliding.
---

# Odoo Repo Orchestrator

Use this skill when the task spans multiple Odoo feature areas.

## Default sequencing

Prefer this dependency order unless the user overrides it:

1. data integrity and reception flows
2. operational state automation
3. backend UX exposure
4. portal exposure
5. accounting/payment overlays
6. external integrations

## Safe parallelization

Can usually run in parallel:

- TecDoc/catalog work
- portal page work
- report/layout work
- audit-log expansion
- document archive work

Avoid parallel edits in the same pass when two streams both touch:

- `sale_order.py`
- stock move/picking logic
- payment/accounting boundary models
- shared portal controller/template files
- manifest/security files

## Ownership rule

When delegating:

- one worker owns the primary model file
- another worker can own views
- one reviewer checks security and upgrade impact

## Before calling a slice complete

Check:

- model logic
- views
- security
- manifest/import wiring
- validation commands
- whether the feature is standard Odoo, custom, or both
