# Automotive Parts Management System - Odoo Integration

Complete ERP system for automotive parts distribution with TecDoc integration.

## Features Implemented

### ✅ Core Functionality
- **Customer Management** with Romanian fields (CUI, CNP, Client Type)
- **Product Management** with TecDoc integration
- **Custom Order Workflow** (Draft → Waiting Supply → Ready → Delivered)
- **NIR (Goods Reception)** with barcode scanning
- **Stock Management** with automatic reservations
- **Audit Log** for all system changes
- **ANAF e-Factura** direct integration (OAuth2 + UBL ingest)
- **Invoice AI fallback** (OpenAI extraction for PDF invoices)

### ✅ TecDoc API Integration (RapidAPI)
- Search products by article number
- Get product details and specifications
- Vehicle compatibility lookup
- VIN decoding
- Manufacturer and supplier data
- Automatic product synchronization

### ✅ Romanian Business Requirements
- CUI validation for companies
- CNP validation for individuals
- Mechanic customer type
- Current balance calculation
- Custom order states in Romanian

## Installation Guide

## Quick start (local)

- Start Odoo: `./dev start`
- Open in browser: `./dev open` (or go to `http://localhost:8069`)
- Update the custom module after changes: `./dev update`
- Tail logs: `./dev logs`
- Tip: if you don’t want logs in the terminal, run `LOG_TO_STDOUT=0 ./dev start`
- Tip: `./dev start` enables auto-reload by default (`--dev=reload`)
- Tip: after pulling code changes that add fields, run `./dev update -d <your_db>` to apply DB schema updates

## Deploy on Railway (self-hosted)

- Deployment guide: `docs/DEPLOY_RAILWAY.md`
- CI/CD: GitHub Actions validates PRs and `main`; Railway should be configured with `Wait for CI` before auto-deploying `main`
- Included deployment artifacts:
  - `Dockerfile`
  - `railway.json`
  - `scripts/railway_start.sh`
  - `scripts/railway_migrate.sh`

## TecDoc data export (from XML)

To enrich an XML like `ART_2026_01_01.xml` with TecDoc details + cross references and save everything to JSON files:

- Set your RapidAPI key as an env var (recommended): `export RAPIDAPI_KEY='...your key...'`
- Python run: `odoo-venv/bin/python scripts/tecdoc_fetch_from_xml.py --xml ART_2026_01_01.xml --resume`
- Node run (recommended): `node scripts/tecdoc_fetch_from_xml.js --xml ART_2026_01_01.xml`
- Outputs: only writes `by_code/<COD>.json` for codes found in TecDoc; everything else goes to `not_found.jsonl` (or scheduled retry via `_progress.json`)
- Optional split (one file per supplier/article match): `node scripts/tecdoc_split_by_supplier.js --out tecdoc_data/art_2026_01_01_js`
- Cross-references for found codes: `node scripts/tecdoc_fetch_xrefs_for_found.js --out tecdoc_data/art_2026_01_01_js` (auto-uses `by_article/` when present; or `--supplier "FEBI BILSTEIN"` for one supplier)
- Odoo Fast Import directory: `tecdoc_data/art_2026_01_01_js` (or point directly to `.../by_article` after splitting)
- Node status (no API calls): `node scripts/tecdoc_fetch_from_xml.js --xml ART_2026_01_01.xml --status --write-remaining tecdoc_data/remaining_codes.txt`
- Output:
  - Python: `tecdoc_data/art_2026_01_01/by_code/<COD>.json`
  - Node: `tecdoc_data/art_2026_01_01_js/by_code/<COD>.json`

Security note: do not hardcode or commit your RapidAPI key.

### 1. Install the Module

1. Go to **http://localhost:8069**
2. Login to your **TecDoc** database
3. Enable **Developer Mode**:
   - Settings → Activate the developer mode
4. Go to **Apps**
5. Click **"Update Apps List"**
6. Search for **"Automotive Parts Management"**
7. Click **"Install"**

### 2. Configure TecDoc API

1. Go to **Automotive Parts → TecDoc → TecDoc Configuration**
2. Click **"Create"**
3. Enter your RapidAPI credentials:
   - **API Key**: `<YOUR_RAPIDAPI_KEY>`
   - **API Host**: `tecdoc-catalog.p.rapidapi.com`
   - **Base URL**: `https://tecdoc-catalog.p.rapidapi.com`
   - **Language ID**: use RapidAPI “Languages” (`/languages/list`) to find the ID for your plan (provider examples often use `4`)
   - **Country Filter ID**: use RapidAPI “Countries” / “Country details” to find your country filter ID (provider examples often use `63`)
4. Click **"Save"**

### 3. Sync Products from TecDoc

1. Go to **Automotive Parts → TecDoc → Sync Products**
2. Enter an **Article Number** (e.g., a brake pad part number)
3. Click **"Sync Product"**
4. The product will be created with TecDoc data

### 4. Configure ANAF e-Factura + OpenAI

1. Export required secrets in your environment (or inject them in deployment secrets):
   - You can start from `.env.example` in repo root.

```bash
export ANAF_EFACTURA_ENV=test
export ANAF_EFACTURA_CUI=12345678
export ANAF_OAUTH_CLIENT_ID=...
export ANAF_OAUTH_CLIENT_SECRET=...
export ANAF_OAUTH_REDIRECT_URI=...
export OPENAI_API_KEY=...
export OPENAI_MODEL=gpt-4o-mini
```

2. Go to **Automotive Parts → ANAF e-Factura → ANAF Configuration**.
3. Click **Load from Env**.
4. Click **Open OAuth Login**, authenticate in ANAF, then paste authorization code.
5. Click **Exchange Auth Code**.
6. Click **Fetch Invoices** to create/update ingest jobs from ANAF messages.

Optional environment overrides:
- `ANAF_OAUTH_AUTHORIZE_URL` (default `https://logincert.anaf.ro/anaf-oauth2/v1/authorize`)
- `ANAF_OAUTH_TOKEN_URL` (default `https://logincert.anaf.ro/anaf-oauth2/v1/token`)
- `ANAF_EFACTURA_ACCESS_TOKEN` / `ANAF_EFACTURA_REFRESH_TOKEN` for bootstrap only

## Usage Guide

### Managing Customers

1. Go to **CRM** or **Sales → Customers**
2. Create a new customer
3. Set **Client Type**:
   - Persoană Fizică (Individual) - requires CNP
   - Persoană Juridică (Company) - requires CUI
   - Mecanic (Mechanic) - for portal access
4. Fill in Romanian fields (CUI/CNP)
5. View **Automotive Info** tab for balance and audit trail

### Managing Products

1. Go to **Inventory → Products**
2. Create or edit a product
3. Go to **TecDoc Integration** tab:
   - Enter **TecDoc ID** or **Article Number**
   - Click **"Sync from TecDoc"** to fetch data
   - View **Vehicle Compatibility**
4. Go to **Stock Auto** tab:
   - View available and reserved stock
   - Generate labels

### Managing Orders

1. Go to **Sales → Orders**
2. Create a new order
3. Select **Order Type** (Internal/External)
4. Add products
5. Order **Auto State** updates automatically based on stock:
   - **În așteptare aprovizionare** - Waiting for stock
   - **Gata de pregătire** - Ready to prepare (stock available)
   - **Livrată** - Delivered
6. View **Stock Status** indicator (Full/Partial/None)

### NIR - Goods Reception

1. Go to **Inventory → Operations → Receipts**
2. Create or open a reception
3. **NIR Number** is generated automatically
4. Use **Scan Barcode** to add products quickly
5. Link **Supplier Invoice** (manual or from ANAF)
6. Click **"Validate"** when done
7. Use **"Print Labels"** to generate product labels

### ANAF / AI Invoice Ingest (replacement for legacy “Import AI” flow)

1. Open **Automotive Parts → ANAF e-Factura → Invoice Ingest Jobs**.
2. For ANAF invoices:
   - fetch from ANAF config (`Fetch Invoices`)
   - open created ingest job and review parsed payload.
3. For supplier PDFs/scans:
   - attach PDF on ingest job
   - click **Extract with OpenAI**
   - review supplier, invoice header, and matched/unmatched lines
   - click **Create Draft Vendor Bill**.
4. Link the bill to NIR receipt via supplier invoice fields / invoice wizard.

### Audit Log

1. Go to **Automotive Parts → Audit Log**
2. View all system changes:
   - User who made the change
   - Action type (Create/Modify/Delete)
   - Timestamp
   - Description

## API Documentation

### TecDoc API Endpoints Used

```python
# Search product
GET /articles/search/lang-id/{langId}/article-search/{articleNo}

# Get product details
GET /articles/article-id-details/{articleId}/lang-id/{langId}/country-filter-id/{countryFilterId}

# Get compatible vehicles
GET /articles/compatible-vehicles/lang-id/{langId}/article-no/{articleNo}

# Decode VIN
GET /vin/decoder-v3/{vinNo}

# Get manufacturers
GET /manufacturers/list-by-type-id/{typeId}

# Get suppliers
GET /suppliers/list
```

### Using TecDoc API from Python

```python
# Get TecDoc API instance
api = env['tecdoc.api'].search([], limit=1)

# Search for a product
results = api.search_article_by_number('ABC123')

# Get product details
details = api.get_article_details(article_id=12345)

# Get compatible vehicles
vehicles = api.get_compatible_vehicles('ABC123')

# Decode VIN
vehicle_info = api.decode_vin('WBA12345678901234')
```

## Module Structure

```
custom_addons/automotive_parts/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   ├── tecdoc_api.py          # TecDoc integration
│   ├── res_partner.py          # Extended customers
│   ├── product_product.py      # Extended products
│   ├── sale_order.py           # Custom order workflow
│   ├── stock_picking.py        # NIR functionality
│   ├── anaf_efactura.py        # ANAF integration
│   ├── invoice_ingest.py       # ANAF/OCR/OpenAI ingest queue
│   └── audit_log.py            # Audit trail
├── views/
│   ├── res_partner_views.xml
│   ├── product_views.xml
│   ├── sale_order_views.xml
│   ├── stock_picking_views.xml
│   ├── invoice_ingest_views.xml
│   ├── tecdoc_views.xml
│   └── menu_views.xml
├── security/
│   └── ir.model.access.csv
└── data/
    ├── product_data.xml
    └── tecdoc_fast_import_cron.xml
```

## Customization Guide

### Adding New TecDoc Fields

Edit `models/product_product.py`:

```python
class ProductProduct(models.Model):
    _inherit = 'product.product'

    # Add new field
    tecdoc_custom_field = fields.Char('Custom Field')
```

Then update `views/product_views.xml` to display it.

### Extending Order Workflow

Edit `models/sale_order.py`:

```python
auto_state = fields.Selection([
    ('draft', 'Draft'),
    # Add new state here
    ('your_state', 'Your State'),
], ...)
```

### Adding MCP Server (Optional)

To use TecDoc via MCP:

```bash
# Add MCP server configuration
claude mcp add --transport http tecdoc-rapidapi https://tecdoc-catalog.p.rapidapi.com \
  --header "x-rapidapi-key: YOUR_KEY" \
  --header "x-rapidapi-host: tecdoc-catalog.p.rapidapi.com"
```

## Troubleshooting

### Module Not Appearing

1. Update Apps List: Apps → Update Apps List
2. Remove filters in Apps search
3. Check Odoo logs: `tail -f odoo_startup.log`

### TecDoc API Errors

- Check API key is correct
- Verify RapidAPI subscription is active
- Check rate limits on your plan

### Database Errors

- Run: `odoo-venv/bin/python odoo/odoo-bin -c odoo.conf -u automotive_parts`
- Check PostgreSQL is running: `brew services list | grep postgresql`

## Next Steps - Future Enhancements

1. **Harden ANAF + ingest UX**
   - production UAT for OAuth/token lifecycle
   - better assisted matching for unknown supplier/products
   - improved reconciliation to NIR

2. **Label Printer Integration**
   - ZPL/EPL support
   - Network printer configuration
   - Batch printing

3. **Supplier API Integration**
   - Real-time stock checking
   - Automated ordering
   - Price synchronization

4. **Mechanic Portal**
   - Custom portal views
   - Order placement
   - Balance viewing

5. **SAGA Export (optional, phase-gated)**
   - minimal export first if accounting requires it
   - reconciliation reports
   - expand only if direct ANAF + Odoo accounting is insufficient

## Support & Documentation

- **Odoo Documentation**: https://www.odoo.com/documentation/
- **TecDoc API**: https://rapidapi.com/ronhartman/api/tecdoc-catalog
- **Module Location**: `/Users/petruinstagram/Desktop/web-apps/odoo-integration/custom_addons/automotive_parts/`

## License

LGPL-3 (same as Odoo)

---

**Built with:** Odoo 18.0, Python 3.12, PostgreSQL, TecDoc RapidAPI

**Author:** Your Company

**Version:** 1.0.0
