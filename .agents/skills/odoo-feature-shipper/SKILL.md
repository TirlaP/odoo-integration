---
name: odoo-feature-shipper
description: Use when adding or changing a feature in this Odoo project. Covers where to place code, how to wire models/views/security/controllers, how to distinguish standard Odoo from true custom scope, and how to validate the feature before calling it done.
---

# Odoo Feature Shipper

Use this skill when implementing product changes in this repository.

## Quick context

- Main custom module today is `custom_addons/automotive_parts/`.
- Standard Odoo apps already provide a large baseline through `Contacts`, `Sales`, `Purchase`, `Inventory`, `Invoicing`, `Portal`, and related dependencies.
- The job is usually to connect and constrain existing Odoo behavior, not build a second ERP inside Odoo.

## Feature workflow checklist

1. **Start from the existing module**
   - Prefer extending the existing addon under `custom_addons/automotive_parts/`.
   - Only create a new module if the responsibility is truly separate.

2. **Find the real baseline**
   - Check what standard Odoo already provides.
   - Check what custom code already overrides or supplements.
   - State the feature as:
     - covered by standard Odoo
     - covered by current custom addon
     - needs integration/configuration
     - needs real new development

3. **Model logic**
   - Put business rules in `models/*.py`, not only in views.
   - Reuse standard anchors first:
     - order/invoice links
     - payment/invoice links
     - stock move and picking truth
     - portal ownership relations

4. **View exposure**
   - Expose backend UI only where the workflow is actually usable.
   - If a feature is meant for portal users, wire the portal page/controller/rules in the same slice.
   - If a feature exists in code but has no action/view/security, it is not finished.

5. **Security**
   - Add ACLs for each new model.
   - Add record rules where visibility is scoped by partner, mechanic, company, or owner.
   - Avoid `sudo()` in user-facing flows unless strictly necessary.

6. **Manifest and load order**
   - Register new Python files in `models/__init__.py`.
   - Register XML in `__manifest__.py`.
   - Ensure inherited views/templates load after the XML ids they depend on.

7. **Validation**
   - Run:
     - `python3 -m py_compile custom_addons/automotive_parts/models/*.py custom_addons/automotive_parts/controllers/*.py`
     - `find custom_addons/automotive_parts -name '*.xml' -print0 | xargs -0 -n1 xmllint --noout`
   - If behavior changed, plan a real module upgrade and UI workflow test.

## Project-specific reminders

- Do not silently change the meaning of `current_balance`; keep accounting and operational balances distinct.
- Do not claim OCR, payment allocation, supplier APIs, or workflow automation are complete unless both code and UI/security wiring prove it.
- For labels, reports, portal workflows, and stock/accounting features, finish the full path, not just the button.
