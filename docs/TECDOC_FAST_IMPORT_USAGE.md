# TecDoc Fast: Usage

## 1) Export TecDoc details from XML (Node)

One command (resume + 5 concurrency + skip xrefs + skip timed-out codes):

```bash
export RAPIDAPI_KEY='...'
node scripts/tecdoc_fetch_from_xml.js --xml ART_2026_01_01.xml
```

Output folder (default):
- `tecdoc_data/art_2026_01_01_js/by_code/*.json` (only `outcome="found"`)
- `tecdoc_data/art_2026_01_01_js/not_found.jsonl`

## 2) Enrich found codes with cross references (Node, second pass)

```bash
export RAPIDAPI_KEY='...'
node scripts/tecdoc_fetch_xrefs_for_found.js --out tecdoc_data/art_2026_01_01_js
```

## Optional: split per supplier/article (recommended)

If you want each code to become multiple files (one per supplier/article match), run:

```bash
node scripts/tecdoc_split_by_supplier.js --out tecdoc_data/art_2026_01_01_js
```

After splitting, `tecdoc_fetch_xrefs_for_found.js` will automatically prefer `by_article/` when present.

If you only want cross-references for a specific supplier (much faster):

```bash
export RAPIDAPI_KEY='...'
node scripts/tecdoc_fetch_xrefs_for_found.js --out tecdoc_data/art_2026_01_01_js --supplier "FEBI BILSTEIN"
```

## 3) Import into Odoo (PostgreSQL via ORM)

1. Start/upgrade the module:

```bash
./dev update -d <your_db_name>
./dev start -d <your_db_name>
```

2. In Odoo UI:
- `Automotive Parts → TecDoc → Fast Import`
- Create a run with:
  - `Directory`: absolute path to `tecdoc_data/art_2026_01_01_js` (recommended), or directly to `.../by_article` after splitting
  - `Batch Size`: e.g. `25`
  - `Import Cross References`: enable after step (2)
- Click `Start` and let the cron process it.

3. Browse:
- `Automotive Parts → TecDoc → Products (Fast)` (search by `TecDoc Lookup`: article/OEM/EAN/cross)
- `Automotive Parts → TecDoc → Variants (Fast)`
