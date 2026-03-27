---
name: odoo-views-security-portal
description: Use when changing Odoo views, ACLs, record rules, controllers, or portal pages. Focuses on safe view inheritance, portal isolation, and avoiding sudo-driven data leaks.
---

# Odoo Views, Security, and Portal

Use this skill for backend views, portal controllers, record rules, and user-visible workflow wiring.

## View rules

- Always confirm the exact inherited anchor exists in the target Odoo version before editing.
- Prefer narrow `xpath` targets over broad replacements.
- Backend actions, stat buttons, tabs, and statusbars must reflect real model state, not aspirational state.
- If a workflow state exists in Python, expose it consistently in both backend and portal where relevant.

## Security rules

Every new business model must be checked for:

- internal ACLs in `ir.model.access.csv`
- portal ACLs only if portal access is intentional
- record rules scoped to the right ownership relation
- multi-company consistency if the model crosses accounting, stock, or documents

## Portal rules

- Do not use `sudo()` in portal search/read paths unless absolutely necessary and then only with explicit secondary filtering.
- Portal domains must be anchored to the correct owner relation, usually a commercial partner or a model-specific visibility relation.
- If a portal page dereferences related models, confirm the portal user can read those models too. Otherwise expose stored related fields on the safe model instead.
- If a feature is supposed to notify portal users, use message types and templates that are actually visible to them.

## Common misses to check

- model exists but no views
- view exists but no ACL
- portal route exists but no record rule
- backend state differs from portal state labels/counts
- template references a field from an inaccessible related model

## Validation

- parse all XML
- inspect controller domains
- inspect record rules
- test one internal user and one portal user path
