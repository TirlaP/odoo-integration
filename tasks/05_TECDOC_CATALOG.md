# 05) TecDoc catalog (imported data → usable ERP data)

This repo supports two TecDoc modes:
- live RapidAPI sync (ad-hoc)
- “TecDoc Fast” local catalog imported from JSON (recommended for speed + rate limiting)

## 5.1 Export pipeline (Node)

- [ ] Export from XML → JSON (per code):
  - `scripts/tecdoc_fetch_from_xml.js`
- [ ] Split per supplier/article (recommended):
  - `scripts/tecdoc_split_by_supplier.js`
- [ ] Fetch cross-references (slow/heavy endpoint):
  - `scripts/tecdoc_fetch_xrefs_for_found.js`

Acceptance:
- `tecdoc_data/.../by_article/*.json` exists and contains one supplier/article per file.

## 5.2 Import into Odoo (TecDoc Fast)

- [ ] In Odoo: **Automotive Parts → TecDoc → Fast Import**
- [ ] Directory = export root or `.../by_article`
- [ ] Run Mode:
  - Full Import (first time)
  - Cross References Only (after xrefs are ready)
- [ ] Validate the DB got data:
  - Products (Catalog)
  - Variants (Fast)
  - Vehicles / OEM / Cross / Specs menus

Acceptance:
- You can search a product via “TecDoc Lookup” (article/OEM/EAN/cross).
- Product form shows variant counts and opens vehicles/oem/cross/spec lists.

## 5.3 Annual update strategy (required by PDF)

- [ ] Define “yearly refresh” procedure:
  - export new dataset
  - import into staging DB
  - validate counts/spot-check
  - purge/replace in prod using purge wizard (archive vs delete strategy)

Acceptance:
- You can repeat a full import without creating duplicates or breaking products.

