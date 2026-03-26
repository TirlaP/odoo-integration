# TecDoc → Odoo “Fast Catalog” Implementation Plan

Date: 2026-02-01  
Scope: Import TecDoc-enriched catalog data into Odoo (PostgreSQL) with a fast browsing/search UI.

## 0) Goals

- **Single source of truth in DB**: all TecDoc data that matters lives in PostgreSQL (via Odoo models).
- **Very fast UX**:
  - Instant list/search (OEM / EAN / article no / cross number).
  - Product form opens quickly even when an article has thousands of compatible vehicles.
- **No live TecDoc calls on normal browsing**: fetch/sync runs in the background; UI reads DB only.
- **Keep raw JSON snapshots** as an audit/debug fallback (optional), but **do not rely on JSON at runtime**.

## 1) What the exported JSON actually contains (current structure)

Per “code” file (when `outcome: "found"`):

- Top-level:
  - `code` (your query, often equals TecDoc `articleNo`)
  - `inputLines[]` (your XML `<Linie>` objects: Cod, Denumire, Pret, Cod_bare, etc.)
  - `tecdoc.articleNumberDetails`:
    - `articleNo`
    - `countArticles`
    - `articles[]` (0..N TecDoc “variants” for this `articleNo`)
  - Each `articles[]` element commonly includes:
    - `articleId`, `articleNo`, `supplierId`, `supplierName`, `articleProductName`
    - `allSpecifications[]` (criteriaName / criteriaValue)
    - `eanNo` (`eanNumbers` sometimes present)
    - `oemNo[]` (oemBrand / oemDisplayNo) — can be large (hundreds)
    - `compatibleCars[]` (vehicleId, modelId, manufacturerName, modelName, typeEngineName, constructionIntervalStart/End) — can be huge (thousands)
    - `s3image` + media filename/type
- After cross-ref enrichment (second pass):
  - `tecdoc.crossReferencesBySupplier[]` (per supplierName request)
  - `tecdoc.articlesEnriched[]` where each TecDoc article includes `crossReferences` (can be null; response often has `articles: null`)

Observed extremes in your dataset sample:
- `compatibleCars`: up to ~2,700+ rows for a single article variant
- `oemNo`: up to ~180+ rows for a single article variant

## 2) Core modeling decision (to avoid duplicates + stay correct)

Important TecDoc reality: **`articleNo` is not unique**; TecDoc can return multiple suppliers/variants for one `articleNo`.

To keep your “articleNo == Cod” mental model while preserving correctness and speed:

- **`product.template` represents your catalog item keyed by `articleNo` (Cod)**.
- A new model **`tecdoc.article_variant`** represents each TecDoc returned variant, uniquely keyed by `articleId` (and also stores `articleNo`, `supplierId`, `supplierName`).
- Product ↔ TecDoc relationship:
  - `product.template` (1) → (many) `tecdoc.article_variant`
  - On the product form, you show variants and let the user pick a “preferred supplier variant” if needed.

This avoids creating duplicate products for the same `articleNo` while still allowing multi-supplier TecDoc data.

## 3) Normalization strategy (fast queries + dedupe at scale)

### 3.1 Vehicles (the big one)

Yes: store vehicles in a separate table and link by ID.

Recommended:
- `tecdoc.vehicle` (unique by `vehicleId`)
  - Fields: `vehicleId` (unique), `modelId`, `manufacturerName`, `modelName`, `typeEngineName`, `constructionIntervalStart`, `constructionIntervalEnd`
- `tecdoc.article_variant_vehicle_rel`:
  - Many2many relation between `tecdoc.article_variant` and `tecdoc.vehicle`

Why:
- Massive dedupe across variants.
- Fast filtering (`vehicleId`, manufacturer/model).
- Product form stays fast by showing counts + smart buttons, not 2,000 inline rows.

### 3.2 OEM numbers

Recommended:
- `tecdoc.oem_number` (unique by `(brand, number_key)`)
  - `brand` (oemBrand)
  - `display_no` (oemDisplayNo as shown)
  - `number_key` (normalized: uppercase, remove spaces/punct) for fast exact matching
- Many2many: `tecdoc.article_variant` ↔ `tecdoc.oem_number`

Why:
- OEM numbers repeat a lot across suppliers and articles.
- You want very fast “search by OEM”; index `number_key`.

### 3.3 Cross references (AM equivalents)

Cross references are **numbers + manufacturers**; they may or may not resolve to TecDoc `articleId`.

Recommended:
- `tecdoc.cross_number` (unique by `(manufacturer, number_key, kind)`)
  - `manufacturer` (crossManufacturerName)
  - `display_no` (crossNumber)
  - `number_key` (normalized)
  - `kind` (optional: IAM/OEM/unknown; keep `searchLevel` too)
- `tecdoc.article_variant_cross_rel`
  - Link `tecdoc.article_variant` → `tecdoc.cross_number`
  - Store `searchLevel` and `source_supplier_id`/`source_supplier_name` (provenance)

Optional “best-of-best” upgrade (phase 3):
- A resolver job that tries to map `cross_number` to a `tecdoc.article_variant` (target) and stores `target_article_variant_id`.

### 3.4 Specifications

Recommended:
- `tecdoc.criteria` (unique by `name_key`)
  - `name` (criteriaName), `name_key` (normalized)
- `tecdoc.article_variant_criteria_value`
  - `article_variant_id`, `criteria_id`, `value_text` (criteriaValue)

Why:
- Criteria names dedupe well; values vary.
- Keeps search/filter possible (e.g. “Dimensiune filet exterior = …”).

### 3.5 EANs / media

- EAN: store as one2many on `tecdoc.article_variant` (`tecdoc.article_variant_ean`)
- Images/media:
  - Start by storing URLs/filenames on `tecdoc.article_variant`
  - Optional: download and store as `ir.attachment` later (only if needed)

### 3.6 Raw JSON snapshots (optional but recommended)

Store raw JSON in `tecdoc.article_variant.raw_json` (JSONB/Text) for:
- Debugging mismatches
- Future re-processing without re-calling TecDoc

But do not read it in normal UI paths.

## 4) Indexing for “instant” search

For speed, assume these are the hot paths:
- Search by `articleNo` / `Cod`
- Search by OEM number
- Search by cross number
- Open product form quickly

Recommended indexes (conceptually; implemented via Odoo SQL constraints / `cr.execute`):
- `tecdoc_article_variant(articleId)` unique
- `tecdoc_article_variant(articleNo_key)` btree (normalized)
- `tecdoc_oem_number(number_key)` btree
- `tecdoc_cross_number(number_key)` btree
- Relation tables: indexes on both FK columns (Odoo typically adds these; verify)

Optional (only if you really need “contains” search):
- Enable `pg_trgm` and add trigram GIN on `display_no` fields
  - This is powerful but adds overhead; do it only if necessary.

## 5) Import/sync workflow (two-phase, scalable)

### Phase A: Import “details” (what you already generated)

Input: `tecdoc_data/.../by_code/*.json` + `not_found.jsonl`

Steps:
1. Create/Update `product.template` by `articleNo` (Cod)
2. Create/Update `tecdoc.article_variant` per TecDoc `articleId`
3. Bulk upsert:
   - OEM numbers + M2M links
   - Criteria + values
   - Vehicles + M2M links (this can be the biggest bulk insert)
4. Store counts on `tecdoc.article_variant` and/or `product.template`:
   - `vehicle_count`, `oem_count`, `spec_count`, `cross_count`
   These should be **stored integers** updated at import time (not computed live).

Implementation detail for speed:
- Prefer bulk SQL inserts (`execute_values`) inside Odoo for large tables instead of ORM row-by-row.

### Phase B: Cross references enrichment

Run a separate job (like you’re doing now) to fill cross refs for variants, then:
- Upsert into `tecdoc.cross_number` + relation table
- Update stored counters

## 6) Odoo UI design (fast by default)

### Product form (`product.template`)

Add a “TecDoc” tab with:
- Variants list (tree) showing: supplier, product name, counts (vehicles/oem/spec/cross)
- Smart buttons:
  - “Vehicles (count)” → opens `tecdoc.vehicle` list filtered by this product/variant
  - “OEM numbers (count)”
  - “Cross refs (count)”
  - “Specs (count)”

Key rule for speed:
- Do **not** render huge one2many lists inline on the main form.
- Always show counts + open dedicated list views.

### Dedicated menus

TecDoc → Articles (variants)  
TecDoc → Vehicles  
TecDoc → OEM Numbers  
TecDoc → Cross Numbers  
TecDoc → Import Issues (from `not_found.jsonl` imports)

### Search UX (what makes it feel “instant”)

Add search boxes and filters that map to indexed fields:
- Search OEM by normalized key (user can type with spaces; you normalize input)
- Search CrossNumber similarly
- Search article by articleNo/Cod

## 7) Keeping it fast long-term

- Never call TecDoc on form open.
- Use background jobs (cron/queue) for refresh.
- Store “denormalized counts” for UI.
- Use exact-match normalized keys + btree indexes.
- Keep the big relations (vehicles) out of the main form; use list views with paging.

## 8) Execution phases (recommended order)

**Phase 1 — Core schema + importer + minimal UI**
- Models: product ↔ article variants
- Specs/OEM/EAN/media
- Fast search by articleNo and OEM

**Phase 2 — Vehicles at scale**
- Vehicle table + M2M
- Smart-button UI for compatibility
- Index tuning

**Phase 3 — Cross references (AM numbers)**
- Cross number table + relation
- Optional resolver to map cross numbers → TecDoc articles

**Phase 4 — Polish**
- Better kanban/list layouts
- Optional CSS tweaks
- Optional attachments for images

## 9) Open questions (to finalize before coding)

1. Product identity:
   - Should `product.template` be created for every `articleNo` found in TecDoc, even if it doesn’t exist in your XML?
2. Supplier preference:
   - Do you want a “preferred supplier variant” per product for pricing/stock, or do you store stock per product only?
3. Vehicles:
   - Do you need vehicle filtering by manufacturer/model in day-to-day operations, or is it mainly “view compatibility list”?

