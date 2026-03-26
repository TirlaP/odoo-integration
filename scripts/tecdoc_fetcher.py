#!/usr/bin/env python3
"""
TecDoc Data Fetcher
Fetches part data from TecDoc API and saves to JSON.

Usage:
    python scripts/tecdoc_fetcher.py --api-key YOUR_RAPIDAPI_KEY

Resume from where it left off:
    python scripts/tecdoc_fetcher.py --api-key YOUR_RAPIDAPI_KEY --resume
"""

import csv
import json
import time
import argparse
import logging
import re
import sys
from pathlib import Path
from datetime import datetime
from urllib.parse import quote
import requests

# Setup logging (console verbosity is configured in main)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('tecdoc_fetch.log')]
)
logger = logging.getLogger(__name__)

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
CSV_FILE = PROJECT_ROOT / "INVENTORY_ROLLBACK_2026_01_01.csv"
OUTPUT_DIR = PROJECT_ROOT / "tecdoc_data"
PROGRESS_FILE = OUTPUT_DIR / "_progress.json"
RESULTS_FILE = OUTPUT_DIR / "tecdoc_results.json"
NOT_FOUND_FILE = OUTPUT_DIR / "not_found.json"
NOT_FOUND_JSONL_FILE = OUTPUT_DIR / "not_found.jsonl"
BY_CODE_DIR = OUTPUT_DIR / "by_code"

# API Config
API_HOST = "tecdoc-catalog.p.rapidapi.com"
BASE_URL = f"https://{API_HOST}"
LANG_ID = 21  # Romanian
COUNTRY_FILTER_ID = 63  # Romania
EAN_FALLBACK_LANG_ID = 4

# Rate limiting
REQUESTS_PER_SECOND = 2  # Adjust based on your RapidAPI plan
REQUEST_DELAY = 1.0 / REQUESTS_PER_SECOND


def safe_filename_fragment(value: str) -> str:
    value = str(value or "").strip()
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    return value or "_"


def choose_part_code(part: dict) -> str:
    return (
        part.get("order_code")
        or part.get("barcode")
        or part.get("internal_code")
        or part.get("original_code")
        or "NO_CODE"
    ).strip()


def append_jsonl(path: Path, payload: dict):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def write_by_code_json(folder: Path, code: str, payload: dict):
    folder.mkdir(parents=True, exist_ok=True)
    file_path = folder / f"{safe_filename_fragment(code)}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def format_duration(seconds: float) -> str:
    if seconds is None or seconds < 0:
        return "unknown"
    s = int(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"{h}h{m:02d}m"
    if m > 0:
        return f"{m}m{sec:02d}s"
    return f"{sec}s"


def format_rate(current: int, elapsed: float) -> str:
    if elapsed <= 0 or current <= 0:
        return "0.00/s"
    return f"{(current / elapsed):.2f}/s"


def render_progress(current: int, total: int, started_at: float, stats: dict, current_code: str = ""):
    if total <= 0:
        return
    pct = (current / total) * 100
    width = 34
    filled = int((pct / 100) * width)
    bar = "[" + ("█" * filled) + ("░" * (width - filled)) + "]"
    elapsed = time.time() - started_at
    avg = (elapsed / current) if current > 0 else 0
    remaining = max(0, total - current)
    eta = avg * remaining
    current_code = (current_code or "").replace("\n", " ").strip()
    if len(current_code) > 24:
        current_code = current_code[:21] + "..."
    line = (
        f"\r{current:>6}/{total:<6} {pct:6.2f}% {bar} "
        f"| ETA {format_duration(eta)} "
        f"| ELAPSED {format_duration(elapsed)} "
        f"| RATE {format_rate(current, elapsed)} "
        f"| found={stats['found']} not_found={stats['not_found']} errors={stats['errors']} skipped={stats['skipped']} "
        f"| code={current_code}"
    )
    print(line, end="", file=sys.stderr, flush=True)


class TecDocFetcher:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            'x-rapidapi-key': api_key,
            'x-rapidapi-host': API_HOST
        })
        self.stats = {
            'total': 0,
            'found': 0,
            'not_found': 0,
            'errors': 0,
            'skipped': 0
        }

    def _request(self, endpoint: str, method: str = 'GET', params: dict = None) -> dict | None:
        """Make API request with error handling."""
        url = f"{BASE_URL}{endpoint}"
        try:
            if method == 'GET':
                resp = self.session.get(url, params=params, timeout=30)
            else:
                resp = self.session.post(url, params=params, timeout=30)

            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                return None
            logger.warning(f"HTTP Error {e.response.status_code}: {endpoint}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            return None
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON response: {endpoint}")
            return None

    def search_by_article_number(self, article_no: str) -> dict | None:
        """Search article by number using article-number-details (requested flow)."""
        encoded = quote(str(article_no), safe='')
        endpoint = (
            f"/articles/article-number-details/type-id/1/lang-id/21/country-filter-id/{COUNTRY_FILTER_ID}"
            f"/article-no/{encoded}"
        )
        return self._request(endpoint)

    def search_by_ean(self, ean: str) -> dict | None:
        """Search article by EAN/barcode (requested fallback endpoint)."""
        encoded = quote(str(ean), safe='')
        endpoint = (
            f"/artlookup/search-articles-by-article-no/lang-id/{EAN_FALLBACK_LANG_ID}"
            f"/article-type/EAN/article-no/{encoded}"
        )
        return self._request(endpoint)

    def get_article_details(self, article_id: int) -> dict | None:
        """Get full article details."""
        endpoint = (
            f"/articles/article-complete-details/type-id/1/article-id/{article_id}"
            f"/lang-id/{LANG_ID}/country-filter-id/{COUNTRY_FILTER_ID}"
        )
        result = self._request(endpoint)
        if result:
            return result
        # Fallback endpoint
        endpoint = (
            f"/articles/article-id-details/{article_id}"
            f"/lang-id/{LANG_ID}/country-filter-id/{COUNTRY_FILTER_ID}"
        )
        return self._request(endpoint)

    def get_compatible_vehicles(self, article_no: str, supplier_id: int = None) -> dict | None:
        """Get compatible vehicles for article."""
        encoded = quote(str(article_no), safe='')
        if supplier_id:
            endpoint = f"/articles/compatible-vehicles/lang-id/{LANG_ID}/article-no/{encoded}/supplier-id/{supplier_id}"
        else:
            endpoint = f"/articles/compatible-vehicles/lang-id/{LANG_ID}/article-no/{encoded}"
        return self._request(endpoint)

    def get_article_media(self, article_id: int) -> dict | None:
        """Get article images/media."""
        endpoint = f"/articles/article-all-media-info/{article_id}/lang-id/{LANG_ID}"
        return self._request(endpoint)

    def get_oem_numbers(self, article_no: str) -> dict | None:
        """Get OEM cross-references."""
        encoded = quote(str(article_no), safe='')
        endpoint = f"/artlookup/search-for-cross-references-through-oem-numbers/article-no/{encoded}/supplierName/"
        return self._request(endpoint)

    @staticmethod
    def _has_articles(data: dict) -> bool:
        """Check if response contains articles."""
        if not data:
            return False
        if isinstance(data, list) and len(data) > 0:
            return True
        if isinstance(data, dict):
            articles = data.get('articles', [])
            if articles:
                return True
            # Check nested data
            nested = data.get('data', {})
            if isinstance(nested, dict) and nested.get('articles'):
                return True
        return False

    @staticmethod
    def _extract_articles(data: dict) -> list:
        """Extract articles list from various response formats."""
        if not data:
            return []
        if isinstance(data, list):
            return [a for a in data if isinstance(a, dict)]
        if isinstance(data, dict):
            articles = data.get('articles', [])
            if articles:
                return [a for a in articles if isinstance(a, dict)]
            nested = data.get('data', {})
            if isinstance(nested, dict):
                return [a for a in nested.get('articles', []) if isinstance(a, dict)]
        return []

    def fetch_complete_data(self, article_no: str, ean: str = None) -> dict | None:
        """Fetch all available data for a part."""
        result = {
            'query': {
                'article_no': article_no,
                'ean': ean,
                'fetched_at': datetime.now().isoformat()
            },
            'search_result': None,
            'article_details': None,
            'compatible_vehicles': None,
            'media': None,
            'oem_references': None
        }

        # Step 1: Search by article number
        search_data = None
        if article_no:
            search_data = self.search_by_article_number(article_no)
            time.sleep(REQUEST_DELAY)

        # Step 2: Try EAN if article number didn't work
        if not self._has_articles(search_data) and ean:
            search_data = self.search_by_ean(ean)
            time.sleep(REQUEST_DELAY)

        if not self._has_articles(search_data):
            return None

        result['search_result'] = search_data
        articles = self._extract_articles(search_data)

        if not articles:
            return result

        # Get first article's ID for detailed lookup
        first_article = articles[0]
        article_id = first_article.get('articleId') or first_article.get('article_id')
        supplier_id = first_article.get('supplierId') or first_article.get('supplier_id')
        resolved_article_no = first_article.get('articleNo') or first_article.get('article_no') or article_no

        # Step 3: Get detailed info
        if article_id:
            result['article_details'] = self.get_article_details(article_id)
            time.sleep(REQUEST_DELAY)

            # Step 4: Get media/images
            result['media'] = self.get_article_media(article_id)
            time.sleep(REQUEST_DELAY)

        # Step 5: Get compatible vehicles
        if resolved_article_no:
            result['compatible_vehicles'] = self.get_compatible_vehicles(
                resolved_article_no,
                supplier_id=supplier_id
            )
            time.sleep(REQUEST_DELAY)

        # Step 6: Get OEM cross-references
        if resolved_article_no:
            result['oem_references'] = self.get_oem_numbers(resolved_article_no)
            time.sleep(REQUEST_DELAY)

        return result


def load_csv(file_path: Path) -> list[dict]:
    """Load inventory CSV."""
    parts = []
    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            parts.append({
                'name': row.get('Nume', '').strip(),
                'internal_code': row.get('Cod intern', '').strip(),
                'original_code': row.get('Cod original', '').strip(),
                'barcode': row.get('Cod de bare', '').strip(),
                'order_code': row.get('Cod comanda', '').strip(),
                'equivalent_codes': row.get('Coduri echivalente', '').strip(),
                'manufacturer': row.get('Producator', '').strip(),
                'category': row.get('Categorie', '').strip(),
                'supplier': row.get('Furnizor', '').strip(),
                'quantity': row.get('Cantitate', '0').strip(),
                'price': row.get('Pret', '0').strip(),
            })
    return parts


def load_progress() -> dict:
    """Load progress from previous run."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, 'r') as f:
            return json.load(f)
    return {'processed': [], 'last_index': 0}


def save_progress(progress: dict):
    """Save progress for resume capability."""
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f)


def save_results(results: list, not_found: list):
    """Save results to JSON files."""
    with open(RESULTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    with open(NOT_FOUND_FILE, 'w', encoding='utf-8') as f:
        json.dump(not_found, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description='Fetch TecDoc data for inventory parts')
    parser.add_argument('--api-key', required=True, help='RapidAPI key for TecDoc')
    parser.add_argument('--resume', action='store_true', help='Resume from last progress')
    parser.add_argument('--limit', type=int, default=0, help='Limit number of parts to process (0 = all)')
    parser.add_argument('--start', type=int, default=0, help='Start from specific index')
    parser.add_argument('--verbose-log', action='store_true', help='Show per-item logs in terminal')
    args = parser.parse_args()

    if args.verbose_log:
        if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
            logger.addHandler(logging.StreamHandler())

    # Setup output directory
    OUTPUT_DIR.mkdir(exist_ok=True)
    BY_CODE_DIR.mkdir(exist_ok=True)
    # Start fresh not_found jsonl if this is a fresh run
    if not args.resume and NOT_FOUND_JSONL_FILE.exists():
        NOT_FOUND_JSONL_FILE.unlink()

    # Load data
    logger.info(f"Loading CSV from {CSV_FILE}")
    parts = load_csv(CSV_FILE)
    logger.info(f"Loaded {len(parts)} parts")

    # Load existing results if resuming
    results = []
    not_found = []
    progress = {'processed': set(), 'last_index': 0}

    if args.resume:
        progress_data = load_progress()
        progress['processed'] = set(progress_data.get('processed', []))
        progress['last_index'] = progress_data.get('last_index', 0)

        if RESULTS_FILE.exists():
            with open(RESULTS_FILE, 'r', encoding='utf-8') as f:
                results = json.load(f)

        if NOT_FOUND_FILE.exists():
            with open(NOT_FOUND_FILE, 'r', encoding='utf-8') as f:
                not_found = json.load(f)

        logger.info(f"Resuming from index {progress['last_index']}, {len(progress['processed'])} already processed")

    # Initialize fetcher
    fetcher = TecDocFetcher(args.api_key)

    # Determine range
    start_idx = args.start if args.start > 0 else progress['last_index']
    end_idx = len(parts) if args.limit == 0 else min(start_idx + args.limit, len(parts))

    logger.info(f"Processing parts {start_idx} to {end_idx}")
    total_in_run = max(0, end_idx - start_idx)
    run_started_at = time.time()

    try:
        for idx in range(start_idx, end_idx):
            part = parts[idx]
            order_code = part['order_code']
            barcode = part['barcode']
            preferred_code = choose_part_code(part)

            # Create unique key for this part
            part_key = f"{order_code}|{barcode}"

            if part_key in progress['processed']:
                logger.debug(f"[{idx}] Skipping already processed: {order_code}")
                fetcher.stats['skipped'] += 1
                render_progress(
                    current=(idx - start_idx + 1),
                    total=total_in_run,
                    started_at=run_started_at,
                    stats=fetcher.stats,
                    current_code=preferred_code,
                )
                continue

            fetcher.stats['total'] += 1

            # Skip if no searchable data
            if not order_code and not barcode:
                logger.info(f"[{idx}] No order code or barcode: {part['name'][:50]}")
                not_found_row = {
                    'index': idx,
                    'part': part,
                    'reason': 'no_searchable_code'
                }
                not_found.append(not_found_row)
                append_jsonl(NOT_FOUND_JSONL_FILE, not_found_row)
                fetcher.stats['not_found'] += 1
                progress['processed'].add(part_key)
                render_progress(
                    current=(idx - start_idx + 1),
                    total=total_in_run,
                    started_at=run_started_at,
                    stats=fetcher.stats,
                    current_code=preferred_code,
                )
                continue

            if args.verbose_log:
                logger.info(f"[{idx}/{end_idx}] Fetching: {order_code or barcode} ({part['name'][:40]}...)")

            try:
                data = fetcher.fetch_complete_data(order_code, barcode)

                if data:
                    found_row = {
                        'index': idx,
                        'part': part,
                        'tecdoc': data
                    }
                    results.append(found_row)
                    write_by_code_json(
                        BY_CODE_DIR,
                        preferred_code,
                        {
                            'code': preferred_code,
                            'outcome': 'found',
                            'source': 'csv',
                            'fetched_at': datetime.now().isoformat(),
                            'input': {
                                'index': idx,
                                'part': part,
                            },
                            'tecdoc': data,
                        }
                    )
                    fetcher.stats['found'] += 1
                    if args.verbose_log:
                        logger.info(f"  -> Found!")
                else:
                    not_found_row = {
                        'index': idx,
                        'part': part,
                        'reason': 'not_in_tecdoc'
                    }
                    not_found.append(not_found_row)
                    append_jsonl(NOT_FOUND_JSONL_FILE, not_found_row)
                    fetcher.stats['not_found'] += 1
                    if args.verbose_log:
                        logger.info(f"  -> Not found in TecDoc")

                progress['processed'].add(part_key)
                progress['last_index'] = idx + 1

            except Exception as e:
                logger.error(f"  -> Error: {e}")
                fetcher.stats['errors'] += 1
                not_found_row = {
                    'index': idx,
                    'part': part,
                    'reason': f'error: {str(e)}'
                }
                not_found.append(not_found_row)
                append_jsonl(NOT_FOUND_JSONL_FILE, not_found_row)

            render_progress(
                current=(idx - start_idx + 1),
                total=total_in_run,
                started_at=run_started_at,
                stats=fetcher.stats,
                current_code=preferred_code,
            )

            # Save progress every 50 parts
            if fetcher.stats['total'] % 50 == 0:
                save_progress({
                    'processed': list(progress['processed']),
                    'last_index': progress['last_index']
                })
                save_results(results, not_found)
                logger.info(f"Progress saved. Stats: {fetcher.stats}")

    except KeyboardInterrupt:
        logger.info("\nInterrupted by user. Saving progress...")

    finally:
        print("", file=sys.stderr)
        # Final save
        save_progress({
            'processed': list(progress['processed']),
            'last_index': progress['last_index']
        })
        save_results(results, not_found)

        logger.info("\n" + "="*50)
        logger.info("FINAL STATS:")
        logger.info(f"  Total processed: {fetcher.stats['total']}")
        logger.info(f"  Found in TecDoc: {fetcher.stats['found']}")
        logger.info(f"  Not found:       {fetcher.stats['not_found']}")
        logger.info(f"  Errors:          {fetcher.stats['errors']}")
        logger.info(f"  Skipped:         {fetcher.stats['skipped']}")
        logger.info(f"\nResults saved to: {RESULTS_FILE}")
        logger.info(f"Not found saved to: {NOT_FOUND_FILE}")
        logger.info(f"Not found jsonl: {NOT_FOUND_JSONL_FILE}")
        logger.info(f"Per-code found dir: {BY_CODE_DIR}")


if __name__ == '__main__':
    main()
