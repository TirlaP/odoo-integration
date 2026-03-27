---
name: odoo-python-backend-best-practices
description: Use when writing or reviewing Python code in Odoo addons. Covers ORM-first patterns, compute/constraint discipline, context safety, small-method structure, and backend quality rules adapted to this repository.
---

# Odoo Python Backend Best Practices

Use this skill for Python work in Odoo models, controllers, and supporting backend code.

## Core rules

- Prefer the ORM over raw SQL.
- Keep methods small and override-friendly.
- Use explicit constraints and computed fields for business invariants.
- Avoid broad exception handling.
- Avoid manual transaction control unless you created your own cursor.

## Model patterns

- Put validation in `@api.constrains` when it is a business invariant.
- Put derived values in computed fields when they are true projections of model state.
- Use `store=True` only when the field truly benefits from storage/searching.
- Be careful with compute dependencies; include the fields that actually drive recomputation.

## Context safety

- Use `with_context(...)` narrowly.
- Do not leak default values or control flags across unrelated nested creates/writes.
- Name control flags explicitly, for example:
  - `skip_audit_log`
  - `skip_auto_state_update`
  - `skip_edit_restriction`

## Odoo-specific Python quality

- No fake execution paths that only notify when the feature should actually run.
- No semantic overload of existing fields just because it is convenient.
- No duplicate business truth across unrelated models if a standard relation already exists.
- For accounting and stock, custom summaries are fine; custom truth replacement is not.

## Review checklist

- Any `sudo()` justified?
- Any company mismatch possible?
- Any partner commercial-entity mismatch possible?
- Any computed field using the wrong semantic source?
- Any portal-visible logic depending on inaccessible relations?
- Any placeholder method still pretending to be a finished feature?

## Validation

Run:

```bash
python3 -m py_compile custom_addons/automotive_parts/models/*.py custom_addons/automotive_parts/controllers/*.py
```

Then inspect the actual calling workflow, not just syntax.
