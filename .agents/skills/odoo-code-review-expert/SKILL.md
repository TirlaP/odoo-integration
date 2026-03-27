---
name: odoo-code-review-expert
description: Expert code review of Odoo changes in this repository. Use when reviewing diffs, branches, or feature slices with focus on correctness, regressions, view wiring, security, accounting/stock boundaries, and upgrade safety.
---

# Odoo Code Review Expert

Review Odoo changes with a bug-first, upgrade-aware mindset.

## Primary review focus

Prioritize:

- correctness bugs
- workflow regressions
- missing security wiring
- broken view inheritance
- portal data leaks
- accounting/stock truth violations
- upgrade hazards
- missing tests or missing validation

## Severity

- `P0`: security issue, accounting corruption, stock corruption, data loss, broken upgrade
- `P1`: logic bug, bad rule, broken workflow, portal leak, broken linkage
- `P2`: maintainability issue, incomplete UI wiring, partial feature exposure
- `P3`: low-signal cleanup or consistency issue

## Review workflow

1. Scope the change
   - `git status -sb`
   - `git diff --stat`
   - `git diff`

2. Identify affected layers
   - models
   - views/templates
   - controllers
   - security
   - manifest/data load order

3. Check Odoo-specific failure modes
   - model exists but no ACL
   - view exists but no action/menu/security
   - portal route exists but related model is unreadable
   - inherited xpath target may not exist in current Odoo version
   - computed field semantics changed without migration intent
   - standard accounting/stock truth replaced by parallel custom truth

4. Check project-specific boundaries
   - `current_balance` must remain accounting balance unless a deliberate migration says otherwise
   - operational payment logic must sit on top of standard accounting
   - stock readiness must reflect real stock moves or reservations
   - portal mechanic visibility must be keyed correctly to `mechanic_partner_id` or related ownership

## Output

Report findings first, ordered by severity, with exact file references. If no findings, say so and mention remaining validation gaps.
