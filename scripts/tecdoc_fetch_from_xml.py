#!/usr/bin/env python3
"""
Fetch TecDoc data for all <Cod> entries in an XML file (e.g. ART_2026_01_01.xml).

For each unique code:
  1) Calls /articles/article-number-details/.../article-no/{code}
  2) For each supplierName returned, calls
     /artlookup/search-for-cross-references-through-oem-numbers/article-no/{code}/supplierName/{supplierName}

Outputs:
  tecdoc_data/<run_name>/by_code/<code>.json   (one file per code)
  tecdoc_data/<run_name>/not_found.jsonl       (codes with no TecDoc results)
  tecdoc_data/<run_name>/_progress.json        (resume support)
  tecdoc_data/<run_name>/summary.json          (run stats)

Usage:
  RAPIDAPI_KEY=... python scripts/tecdoc_fetch_from_xml.py --xml ART_2026_01_01.xml --resume
  python scripts/tecdoc_fetch_from_xml.py --api-key ... --xml ART_2026_01_01.xml --limit 100
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
import re
from xml.etree.ElementTree import iterparse

import requests

PROJECT_ROOT = Path(__file__).parent.parent


def _json_log(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    payload = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "event": event,
        **fields,
    }
    logger.log(level, json.dumps(payload, ensure_ascii=False))


def normalize_supplier_name(name: str) -> str:
    # The RapidAPI TecDoc endpoint examples often use supplier names without spaces/punctuation
    # (e.g. "FEBI BILSTEIN" -> "FEBIBILSTEIN").
    return "".join(ch for ch in (name or "") if ch.isalnum()).upper()


def safe_filename_fragment(value: str) -> str:
    value = str(value).strip()
    # Avoid path traversal / weird filesystem chars.
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    return value or "_"


def safe_text(value: str | None) -> str:
    return (value or "").strip()


def as_list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [x for x in value if isinstance(x, dict)]


def load_xml_lines(xml_path: Path) -> tuple[dict[str, list[dict[str, str]]], list[str]]:
    """
    Returns:
      - mapping code -> list of input line dicts (all fields as strings)
      - list of codes in appearance order (may include duplicates)
    """
    by_code: dict[str, list[dict[str, str]]] = defaultdict(list)
    ordered_codes: list[str] = []

    # Stream parse for safety (file can be large).
    for _event, elem in iterparse(xml_path, events=("end",)):
        if elem.tag != "Linie":
            continue

        line: dict[str, str] = {}
        for child in list(elem):
            line[child.tag] = safe_text(child.text)

        code = safe_text(line.get("Cod"))
        if code:
            by_code[code].append(line)
            ordered_codes.append(code)

        elem.clear()

    return dict(by_code), ordered_codes


@dataclass(frozen=True)
class TecDocConfig:
    api_key: str
    api_host: str = "tecdoc-catalog.p.rapidapi.com"
    type_id: int = 1
    lang_id: int = 21
    country_filter_id: int = 63
    requests_per_second: float = 2.0
    timeout_seconds: int = 30
    max_retries: int = 5

    @property
    def base_url(self) -> str:
        return f"https://{self.api_host}"

    @property
    def request_delay_seconds(self) -> float:
        if self.requests_per_second <= 0:
            return 0.0
        return 1.0 / self.requests_per_second


class TecDocClient:
    def __init__(self, cfg: TecDocConfig, logger: logging.Logger):
        self.cfg = cfg
        self.logger = logger
        self.session = requests.Session()
        self.session.headers.update(
            {
                "x-rapidapi-key": cfg.api_key,
                "x-rapidapi-host": cfg.api_host,
            }
        )

    def _sleep_rate_limit(self) -> None:
        delay = self.cfg.request_delay_seconds
        if delay > 0:
            time.sleep(delay)

    def _request_json(self, path: str) -> dict[str, Any] | list[Any] | None:
        url = f"{self.cfg.base_url}{path}"
        backoff = 1.0

        for attempt in range(1, self.cfg.max_retries + 1):
            start = time.time()
            try:
                resp = self.session.get(url, timeout=self.cfg.timeout_seconds)
                duration_ms = int((time.time() - start) * 1000)

                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    wait_s = float(retry_after) if retry_after and retry_after.isdigit() else backoff
                    _json_log(
                        self.logger,
                        logging.WARNING,
                        "tecdoc_rate_limited",
                        url=url,
                        attempt=attempt,
                        wait_seconds=wait_s,
                        status_code=resp.status_code,
                        duration_ms=duration_ms,
                    )
                    time.sleep(wait_s)
                    backoff = min(backoff * 2.0, 60.0)
                    continue

                if 500 <= resp.status_code <= 599:
                    _json_log(
                        self.logger,
                        logging.WARNING,
                        "tecdoc_server_error",
                        url=url,
                        attempt=attempt,
                        status_code=resp.status_code,
                        duration_ms=duration_ms,
                    )
                    time.sleep(backoff)
                    backoff = min(backoff * 2.0, 60.0)
                    continue

                if resp.status_code == 404:
                    return None

                resp.raise_for_status()

                try:
                    return resp.json()
                except json.JSONDecodeError:
                    _json_log(
                        self.logger,
                        logging.WARNING,
                        "tecdoc_invalid_json",
                        url=url,
                        attempt=attempt,
                        duration_ms=duration_ms,
                    )
                    return None

            except requests.exceptions.RequestException as exc:
                _json_log(
                    self.logger,
                    logging.WARNING,
                    "tecdoc_request_failed",
                    url=url,
                    attempt=attempt,
                    error=str(exc),
                )
                time.sleep(backoff)
                backoff = min(backoff * 2.0, 60.0)
            finally:
                self._sleep_rate_limit()

        return None

    def article_number_details(self, article_no: str) -> dict[str, Any] | None:
        encoded = quote(str(article_no), safe="")
        path = (
            f"/articles/article-number-details/type-id/{self.cfg.type_id}"
            f"/lang-id/{self.cfg.lang_id}/country-filter-id/{self.cfg.country_filter_id}"
            f"/article-no/{encoded}"
        )
        data = self._request_json(path)
        return data if isinstance(data, dict) else None

    def article_id_details(self, article_id: int) -> dict[str, Any] | None:
        encoded = quote(str(article_id), safe="")
        path = f"/articles/article-id-details/{encoded}/lang-id/{self.cfg.lang_id}/country-filter-id/{self.cfg.country_filter_id}"
        data = self._request_json(path)
        return data if isinstance(data, dict) else None

    def cross_references(self, article_no: str, supplier_name: str) -> dict[str, Any] | None:
        encoded_article = quote(str(article_no), safe="")
        encoded_supplier = quote(str(supplier_name), safe="")
        path = (
            "/artlookup/search-for-cross-references-through-oem-numbers"
            f"/article-no/{encoded_article}/supplierName/{encoded_supplier}"
        )
        data = self._request_json(path)
        return data if isinstance(data, dict) else None

    def search_articles_by_article_no(self, article_no: str, article_type: str = "ArticleNumber") -> dict[str, Any] | None:
        encoded = quote(str(article_no), safe="")
        encoded_type = quote(str(article_type), safe="")
        path = f"/artlookup/search-articles-by-article-no/lang-id/{self.cfg.lang_id}/article-type/{encoded_type}/article-no/{encoded}"
        data = self._request_json(path)
        return data if isinstance(data, dict) else None

    def search_articles(self, article_search_nr: str) -> dict[str, Any] | None:
        encoded = quote(str(article_search_nr), safe="")
        path = f"/articles/search/lang-id/{self.cfg.lang_id}/article-search/{encoded}"
        data = self._request_json(path)
        return data if isinstance(data, dict) else None

    def search_oem_by_article_oem_no(self, article_oem_no: str) -> dict[str, Any] | None:
        encoded = quote(str(article_oem_no), safe="")
        path = f"/articles-oem/search-by-article-oem-no/lang-id/{self.cfg.lang_id}/article-oem-no/{encoded}"
        data = self._request_json(path)
        return data if isinstance(data, dict) else None


def _has_articles(article_number_details: dict[str, Any] | None) -> bool:
    if not article_number_details:
        return False
    articles = as_list_of_dicts(article_number_details.get("articles"))
    return len(articles) > 0


def _dedupe_cross_refs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = (
            safe_text(str(item.get("crossManufacturerName"))),
            safe_text(str(item.get("crossNumber"))),
            safe_text(str(item.get("searchLevel"))),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_progress(progress_path: Path) -> set[str]:
    if not progress_path.exists():
        return set()
    with open(progress_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    processed = data.get("processed_codes", [])
    return set(str(x) for x in processed if x)


def save_progress(progress_path: Path, processed_codes: set[str]) -> None:
    tmp = progress_path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            {
                "processed_codes": sorted(processed_codes),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    tmp.replace(progress_path)


def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch TecDoc data for codes from an XML file")
    parser.add_argument("--xml", type=str, default=str(PROJECT_ROOT / "ART_2026_01_01.xml"))
    parser.add_argument("--out", type=str, default=str(PROJECT_ROOT / "tecdoc_data" / "art_2026_01_01"))
    parser.add_argument("--api-key", type=str, default=os.environ.get("RAPIDAPI_KEY", ""))
    parser.add_argument("--type-id", type=int, default=1)
    parser.add_argument("--lang-id", type=int, default=21)
    parser.add_argument("--country-filter-id", type=int, default=63)
    parser.add_argument("--rps", type=float, default=2.0, help="Requests per second (rate limit)")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout seconds")
    parser.add_argument("--retries", type=int, default=5, help="Max retries for 429/5xx/timeouts")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of unique codes (0 = all)")
    parser.add_argument("--resume", action="store_true", help="Resume using _progress.json and existing by_code files")
    parser.add_argument("--force", action="store_true", help="Re-fetch even if already processed / output exists")
    parser.add_argument("--codes", type=str, default="", help="Comma-separated list of codes to process (others skipped)")
    parser.add_argument("--dry-run", action="store_true", help="Only parse XML and show counts")
    args = parser.parse_args()

    xml_path = Path(args.xml)
    out_dir = Path(args.out)
    by_code_dir = out_dir / "by_code"
    progress_path = out_dir / "_progress.json"
    not_found_path = out_dir / "not_found.jsonl"
    summary_path = out_dir / "summary.json"
    log_path = out_dir / "tecdoc_fetch_xml.log"

    ensure_dir(out_dir)
    ensure_dir(by_code_dir)

    # Logging: JSON lines to file + readable console
    logger = logging.getLogger("tecdoc_fetch_from_xml")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    formatter = logging.Formatter("%(message)s")

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(formatter)
    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    logger.addHandler(fh)
    logger.addHandler(sh)

    if not xml_path.exists():
        _json_log(logger, logging.ERROR, "xml_missing", path=str(xml_path))
        return 2

    if not args.api_key and not args.dry_run:
        _json_log(
            logger,
            logging.ERROR,
            "missing_api_key",
            hint="Pass --api-key or set RAPIDAPI_KEY env var",
        )
        return 2

    _json_log(logger, logging.INFO, "xml_parse_start", path=str(xml_path))
    xml_by_code, ordered_codes = load_xml_lines(xml_path)
    unique_codes = list(xml_by_code.keys())
    _json_log(
        logger,
        logging.INFO,
        "xml_parse_done",
        total_lines=len(ordered_codes),
        unique_codes=len(unique_codes),
    )

    if args.dry_run:
        return 0

    if args.limit and args.limit > 0:
        unique_codes = unique_codes[: args.limit]

    codes_filter: set[str] | None = None
    if args.codes.strip():
        codes_filter = {c.strip() for c in args.codes.split(",") if c.strip()}

    cfg = TecDocConfig(
        api_key=args.api_key,
        type_id=args.type_id,
        lang_id=args.lang_id,
        country_filter_id=args.country_filter_id,
        requests_per_second=args.rps,
        timeout_seconds=max(1, int(args.timeout)),
        max_retries=max(1, int(args.retries)),
    )
    client = TecDocClient(cfg, logger=logger)

    processed_codes: set[str] = set()
    if args.resume:
        processed_codes = load_progress(progress_path)

    stats = {
        "total_unique_codes": len(unique_codes),
        "processed": 0,
        "skipped": 0,
        "found": 0,
        "not_found": 0,
        "errors": 0,
        "cross_ref_calls": 0,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }

    for idx, code in enumerate(unique_codes, start=1):
        code = str(code).strip()
        out_file = by_code_dir / f"{safe_filename_fragment(code)}.json"

        if codes_filter is not None and code not in codes_filter:
            continue

        if not args.force and args.resume and (code in processed_codes or out_file.exists()):
            stats["skipped"] += 1
            continue

        start = time.time()
        cross_ref_results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        _json_log(
            logger,
            logging.INFO,
            "tecdoc_fetch_code_start",
            idx=idx,
            total=len(unique_codes),
            code=code,
        )

        details = client.article_number_details(code)

        resolution: dict[str, Any] = {"strategy": "article-number-details", "resolved_candidates": []}

        if not _has_articles(details):
            # Fallback: some XML codes are EAN/OEM/internal; try searching to resolve to real TecDoc article numbers.
            resolution["strategy"] = "fallback_search"
            candidates: list[dict[str, Any]] = []

            # 1) Try global article search.
            search_resp = client.search_articles(code)
            for a in as_list_of_dicts((search_resp or {}).get("articles")):
                candidates.append(a)
            resolution["articles_search"] = search_resp

            # 2) Try artlookup search by article no with common types.
            # - ArticleNumber: plain TecDoc numbers
            # - EANNumber: barcodes
            # - IAMNumber: aftermarket numbers (provider dependent)
            for article_type in ["ArticleNumber", "EANNumber", "IAMNumber"]:
                art_resp = client.search_articles_by_article_no(code, article_type=article_type)
                resolution[f"artlookup_{article_type}"] = art_resp
                for a in as_list_of_dicts((art_resp or {}).get("articles")):
                    candidates.append(a)

            # 3) Try OEM search endpoint (provider dependent).
            oem_resp = client.search_oem_by_article_oem_no(code)
            resolution["oem_search"] = oem_resp
            for a in as_list_of_dicts((oem_resp or {}).get("articles")):
                candidates.append(a)

            # Deduplicate candidates by (articleNo, supplierName, articleId)
            seen_cand: set[tuple[str, str, str]] = set()
            resolved: list[dict[str, Any]] = []
            for a in candidates:
                article_no = safe_text(a.get("articleNo") or a.get("article_no"))
                supplier_name = safe_text(a.get("supplierName") or a.get("supplier_name"))
                article_id = str(a.get("articleId") or a.get("article_id") or "")
                key = (article_no, supplier_name, article_id)
                if key in seen_cand:
                    continue
                seen_cand.add(key)
                resolved.append(
                    {
                        "articleNo": article_no or None,
                        "supplierName": supplier_name or None,
                        "articleId": int(article_id) if article_id.isdigit() else None,
                        "raw": a,
                    }
                )

            resolution["resolved_candidates"] = resolved

            # Try fetching article-number-details using resolved articleNos
            for cand in resolved:
                article_no = cand.get("articleNo")
                if not article_no:
                    continue
                details = client.article_number_details(str(article_no))
                if _has_articles(details):
                    resolution["resolved_via"] = "articleNo"
                    resolution["resolved_articleNo"] = article_no
                    break

        if not _has_articles(details):
            stats["not_found"] += 1
            stats["processed"] += 1
            processed_codes.add(code)
            save_progress(progress_path, processed_codes)
            append_jsonl(
                not_found_path,
                {
                    "code": code,
                    "reason": "no_articles",
                    "tecdoc_response": details,
                    "inputLines": xml_by_code.get(code, []),
                    "resolution": resolution,
                    "fetched_at": datetime.now().isoformat(timespec="seconds"),
                },
            )
            _json_log(
                logger,
                logging.INFO,
                "tecdoc_fetch_code_done",
                code=code,
                outcome="not_found",
                duration_ms=int((time.time() - start) * 1000),
            )
            continue

        try:
            articles = as_list_of_dicts(details.get("articles"))
            supplier_names = sorted(
                {safe_text(a.get("supplierName")) for a in articles if safe_text(a.get("supplierName"))}
            )

            lookup_article_no = safe_text(details.get("articleNo")) or safe_text(resolution.get("resolved_articleNo")) or code

            # Fetch cross refs per supplier (dedup supplier names).
            for supplier_name in supplier_names:
                variants = []
                raw_variant = supplier_name
                normalized_variant = normalize_supplier_name(supplier_name)
                last_resp: dict[str, Any] | None = None

                # Try both: raw and normalized (some endpoints expect normalized).
                for variant in [raw_variant, normalized_variant]:
                    if not variant:
                        continue
                    variants.append(variant)
                    stats["cross_ref_calls"] += 1
                    resp = client.cross_references(lookup_article_no, variant)
                    if resp:
                        last_resp = resp
                    resp_articles = as_list_of_dicts((resp or {}).get("articles"))
                    if resp and len(resp_articles) > 0:
                        # Success: keep best response and stop trying variants.
                        cross_ref_results.append(
                            {
                                "supplierName": supplier_name,
                                "supplierNameVariantsTried": variants,
                                "response": {
                                    **resp,
                                    "articles": _dedupe_cross_refs(resp_articles),
                                },
                            }
                        )
                        break
                else:
                    last_articles = as_list_of_dicts((last_resp or {}).get("articles"))
                    cross_ref_results.append(
                        {
                            "supplierName": supplier_name,
                            "supplierNameVariantsTried": variants,
                            "response": (
                                {
                                    **last_resp,
                                    "articles": _dedupe_cross_refs(last_articles),
                                }
                                if isinstance(last_resp, dict)
                                else None
                            ),
                        }
                    )

            enriched_articles = []
            by_supplier = {x["supplierName"]: x for x in cross_ref_results}
            for a in articles:
                supplier_name = safe_text(a.get("supplierName"))
                enriched_articles.append(
                    {
                        **a,
                        "crossReferences": by_supplier.get(supplier_name, {}).get("response"),
                    }
                )

            payload = {
                "code": code,
                "inputLines": xml_by_code.get(code, []),
                "tecdoc": {
                    "articleNumberDetails": details,
                    "crossReferencesBySupplier": cross_ref_results,
                    "articlesEnriched": enriched_articles,
                    "resolution": resolution,
                },
                "meta": {
                    "fetched_at": datetime.now().isoformat(timespec="seconds"),
                    "duration_ms": int((time.time() - start) * 1000),
                    "lang_id": cfg.lang_id,
                    "country_filter_id": cfg.country_filter_id,
                    "type_id": cfg.type_id,
                    "errors": errors,
                },
            }

            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

            stats["found"] += 1
            stats["processed"] += 1
            processed_codes.add(code)

            _json_log(
                logger,
                logging.INFO,
                "tecdoc_fetch_code_done",
                code=code,
                outcome="found",
                suppliers=len(supplier_names),
                duration_ms=payload["meta"]["duration_ms"],
            )

        except Exception as exc:
            stats["errors"] += 1
            stats["processed"] += 1
            errors.append({"error": str(exc)})
            append_jsonl(
                not_found_path,
                {
                    "code": code,
                    "reason": "error",
                    "error": str(exc),
                    "inputLines": xml_by_code.get(code, []),
                    "resolution": resolution,
                    "fetched_at": datetime.now().isoformat(timespec="seconds"),
                },
            )
            _json_log(
                logger,
                logging.ERROR,
                "tecdoc_fetch_code_done",
                code=code,
                outcome="error",
                error=str(exc),
                duration_ms=int((time.time() - start) * 1000),
            )

        # Save progress every 25 codes (and at errors)
        if idx % 25 == 0:
            save_progress(progress_path, processed_codes)

    save_progress(progress_path, processed_codes)
    stats["finished_at"] = datetime.now().isoformat(timespec="seconds")

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    _json_log(logger, logging.INFO, "run_done", **stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
