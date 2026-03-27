---
name: odoo-audit-gap-verification
description: Use when auditing an Odoo codebase against requirements, implementation-gap documents, or client claims. Focuses on evidence-based verification, skepticism, and distinguishing standard Odoo from real custom implementation.
---

# Odoo Audit and Gap Verification

Use this skill for audit-only or verification-heavy tasks.

## Core mindset

- Be skeptical.
- Do not assume a feature exists because a field, menu, button, or empty model exists.
- Distinguish clearly between:
  - standard Odoo behavior
  - custom addon behavior
  - partial wiring
  - missing implementation

## Audit workflow

Start with:

1. `__manifest__.py`
2. `models/*.py`
3. `views/*.xml`
4. `controllers/*.py`
5. `security/*`
6. any referenced standard Odoo module only as needed
7. the claimed gap/assessment document

## What counts as partial

Mark a feature as partial if any of these are true:

- model logic exists but no view/action/security wiring
- view exists but no execution logic
- controller exists but relies on unsafe `sudo()` or missing rules
- feature is mostly standard Odoo, with only cosmetic custom fields
- background logic exists but is not connected to the intended business flow

## What to call out explicitly

- overstated implementation claims
- hidden dependencies on standard modules
- portal visibility mismatches
- security blind spots
- missing upgrade/test coverage
- database-state claims that are not provable from source alone

## Output rule

State clearly for each area:

- claimed
- verified in code
- final status: implemented / partial / missing

If the truth is “standard Odoo already covers most of this”, say that directly.
