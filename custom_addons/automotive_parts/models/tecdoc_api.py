# -*- coding: utf-8 -*-
import base64
import requests
import logging
from io import BytesIO
from urllib.parse import quote
from odoo import models, fields, api
from odoo.exceptions import UserError
from .tecdoc_fast_models import _normalize_key

try:
    from PIL import Image, features as pil_features
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    pil_features = None
    Image = None

_logger = logging.getLogger(__name__)

DEFAULT_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_REQUEST_TIMEOUT_SECONDS = 20
TECDOC_CATALOG_HOST = 'tecdoc-catalog.p.rapidapi.com'
TECDOC_CATALOG_BASE_URL = f'https://{TECDOC_CATALOG_HOST}'
AUTO_PARTS_CATALOG_HOST = 'auto-parts-catalog.p.rapidapi.com'
AUTO_PARTS_CATALOG_BASE_URL = f'https://{AUTO_PARTS_CATALOG_HOST}'


class TecDocAPI(models.Model):
    """TecDoc API Integration via RapidAPI"""
    _name = 'tecdoc.api'
    _description = 'TecDoc API Integration'

    name = fields.Char('Name', default='Auto Parts Catalog API')
    provider = fields.Selection(
        [
            ('auto_parts_catalog', 'Auto Parts Catalog (RapidAPI)'),
            ('tecdoc_catalog', 'TecDoc Catalog (legacy RapidAPI)'),
            ('custom', 'Custom RapidAPI host'),
        ],
        string='Provider',
        default='auto_parts_catalog',
    )
    is_default = fields.Boolean(
        'Use for Lookups',
        default=True,
        help='Use this configuration for invoice ingest, product sync, and order-line TecDoc lookup.',
    )
    api_key = fields.Char('RapidAPI Key', required=True)
    api_host = fields.Char('API Host', default=AUTO_PARTS_CATALOG_HOST)
    base_url = fields.Char('Base URL', default=AUTO_PARTS_CATALOG_BASE_URL)
    # RapidAPI TecDoc Catalog uses numeric IDs for language and country filter.
    # The provider examples commonly use lang_id=4 and country_filter_id=63; adjust as needed in UI.
    lang_id = fields.Integer('Language ID', default=4)
    country_filter_id = fields.Integer('Country Filter ID', default=63)

    cache_enabled = fields.Boolean('Cache Enabled', default=True)
    cache_ttl_seconds = fields.Integer('Cache TTL (seconds)', default=DEFAULT_CACHE_TTL_SECONDS)
    cache_allow_stale_on_error = fields.Boolean('Allow Stale Cache on Error', default=True)

    download_images = fields.Boolean('Download Images', default=False)
    overwrite_images = fields.Boolean('Overwrite Existing Images', default=False)

    @api.model
    def _provider_defaults(self, provider):
        defaults = {
            'auto_parts_catalog': {
                'name': 'Auto Parts Catalog API',
                'api_host': AUTO_PARTS_CATALOG_HOST,
                'base_url': AUTO_PARTS_CATALOG_BASE_URL,
            },
            'tecdoc_catalog': {
                'name': 'TecDoc Catalog API',
                'api_host': TECDOC_CATALOG_HOST,
                'base_url': TECDOC_CATALOG_BASE_URL,
            },
        }
        return defaults.get(provider or 'auto_parts_catalog', {})

    @api.onchange('provider')
    def _onchange_provider(self):
        for rec in self:
            defaults = rec._provider_defaults(rec.provider)
            if defaults:
                rec.name = defaults['name']
                rec.api_host = defaults['api_host']
                rec.base_url = defaults['base_url']

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            provider = vals.get('provider') or 'auto_parts_catalog'
            defaults = self._provider_defaults(provider)
            if defaults:
                vals.setdefault('api_host', defaults['api_host'])
                vals.setdefault('base_url', defaults['base_url'])
                vals.setdefault('name', defaults['name'])
            if 'is_default' not in vals and self.search_count([]):
                vals['is_default'] = False
        records = super().create(vals_list)
        records._normalize_default_flags()
        return records

    def write(self, vals):
        if vals.get('provider') and vals['provider'] != 'custom':
            defaults = self._provider_defaults(vals['provider'])
            vals = dict(vals)
            vals.setdefault('api_host', defaults.get('api_host'))
            vals.setdefault('base_url', defaults.get('base_url'))
            vals.setdefault('name', defaults.get('name'))
        result = super().write(vals)
        if vals.get('is_default'):
            self._normalize_default_flags()
        return result

    def _normalize_default_flags(self):
        default_records = self.filtered('is_default')
        if not default_records:
            return
        keep = default_records[:1]
        (self.search([('is_default', '=', True), ('id', 'not in', keep.ids)])).write({'is_default': False})

    @api.model
    def _get_default_api(self):
        api = self.sudo().search([('is_default', '=', True)], order='id', limit=1)
        if api:
            return api
        return self.sudo().search([], order='id', limit=1)

    @api.model
    def _upgrade_default_provider_to_auto_parts_catalog(self):
        """Move legacy RapidAPI TecDoc Catalog configs to the current provider host."""
        legacy_records = self.sudo().search([
            '|',
            ('api_host', '=', TECDOC_CATALOG_HOST),
            ('base_url', '=', TECDOC_CATALOG_BASE_URL),
        ])
        for rec in legacy_records:
            rec.write({
                'provider': 'auto_parts_catalog',
                'api_host': AUTO_PARTS_CATALOG_HOST,
                'base_url': AUTO_PARTS_CATALOG_BASE_URL,
                'name': rec.name or 'Auto Parts Catalog API',
            })
        auto_records = self.sudo().search([
            ('provider', 'in', [False, 'custom']),
            '|',
            ('api_host', '=', AUTO_PARTS_CATALOG_HOST),
            ('base_url', '=', AUTO_PARTS_CATALOG_BASE_URL),
        ])
        for rec in auto_records:
            rec.write({'provider': 'auto_parts_catalog'})
        if not self.sudo().search([('is_default', '=', True)], limit=1):
            first = self.sudo().search([], order='id', limit=1)
            if first:
                first.write({'is_default': True})
        return True

    def action_use_auto_parts_catalog(self):
        self.ensure_one()
        self.write({
            'provider': 'auto_parts_catalog',
            'api_host': AUTO_PARTS_CATALOG_HOST,
            'base_url': AUTO_PARTS_CATALOG_BASE_URL,
            'is_default': True,
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'TecDoc',
                'message': 'Auto Parts Catalog is now the default lookup provider.',
                'type': 'success',
                'sticky': False,
            },
        }

    @staticmethod
    def _path(value):
        return quote(str(value), safe='')

    def _get_headers(self):
        """Get API request headers"""
        api_key = self.env.context.get('tecdoc_api_key_override') or self.api_key
        api_host = self.env.context.get('tecdoc_api_host_override') or self.api_host
        return {
            'x-rapidapi-key': api_key,
            'x-rapidapi-host': api_host
        }

    def action_test_connection(self):
        """Quick sanity-check that the configured RapidAPI key/host works."""
        self.ensure_one()
        payload = self.with_context(tecdoc_no_cache=True)._make_request('/languages/list')

        count = None
        if isinstance(payload, list):
            count = len(payload)
        elif isinstance(payload, dict):
            for key in ('languages', 'data', 'result'):
                value = payload.get(key)
                if isinstance(value, list):
                    count = len(value)
                    break

        msg = "TecDoc API OK."
        if count is not None:
            msg = f"TecDoc API OK (languages: {count})."

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'TecDoc',
                'message': msg,
                'type': 'success',
                'sticky': False,
            }
        }

    def _cache_ttl_for_endpoint(self, endpoint):
        self.ensure_one()
        if not endpoint:
            return self.cache_ttl_seconds or DEFAULT_CACHE_TTL_SECONDS

        if '/articles/search/' in endpoint or '/articles/search-by-article-no/' in endpoint or '/articles/search-by-articles-no-supplier-id/' in endpoint:
            return 24 * 60 * 60
        if '/articles/compatible-vehicles/' in endpoint:
            return 30 * 24 * 60 * 60
        if '/vin/' in endpoint:
            return 365 * 24 * 60 * 60

        return self.cache_ttl_seconds or DEFAULT_CACHE_TTL_SECONDS

    def _make_request(self, endpoint, params=None, method='GET', json_data=None, form_data=None):
        """Make API request to TecDoc (cached)."""
        self.ensure_one()
        params = params or {}
        method = (method or 'GET').upper()

        if method not in {'GET', 'POST'}:
            raise UserError(f'Unsupported TecDoc HTTP method: {method}')

        body_for_cache = None
        if json_data is not None:
            body_for_cache = json_data
        elif form_data is not None:
            body_for_cache = form_data

        cache_model = self.env['tecdoc.api.cache']
        cached = None
        if self.cache_enabled and self.env.context.get('tecdoc_no_cache') is not True:
            cached = cache_model.get_cached(self, method, endpoint, params, body_for_cache, include_expired=False)
            if cached is not None:
                return cached

        try:
            base_url = self.env.context.get('tecdoc_base_url_override') or self.base_url
            url = f"{base_url}{endpoint}"
            headers = self._get_headers()
            if method == 'GET':
                response = requests.get(
                    url,
                    headers=headers,
                    params=params or {},
                    timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS,
                )
            else:
                if form_data is not None:
                    headers = dict(headers, **{'Content-Type': 'application/x-www-form-urlencoded'})
                if json_data is not None:
                    response = requests.post(
                        url,
                        headers=headers,
                        params=params or {},
                        json=json_data,
                        timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS,
                    )
                else:
                    response = requests.post(
                        url,
                        headers=headers,
                        params=params or {},
                        data=form_data or {},
                        timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS,
                    )
            response.raise_for_status()
            data = response.json()

            if self.cache_enabled and self.env.context.get('tecdoc_no_cache') is not True:
                cache_model.set_cached(
                    api_record=self,
                    method=method,
                    endpoint=endpoint,
                    params=params,
                    body=body_for_cache,
                    response_data=data,
                    ok=True,
                    status_code=response.status_code,
                    ttl_seconds=self._cache_ttl_for_endpoint(endpoint),
                )

            return data
        except requests.exceptions.RequestException as e:
            error_text = str(e)
            status_code = getattr(getattr(e, 'response', None), 'status_code', None)
            response_text = getattr(getattr(e, 'response', None), 'text', None)
            if response_text and len(response_text) > 600:
                response_text = response_text[:600] + '…'
            _logger.error(
                "TecDoc API Error: %s%s%s",
                error_text,
                f" (status={status_code})" if status_code else "",
                f" response={response_text!r}" if response_text else "",
            )

            if self.cache_allow_stale_on_error and self.cache_enabled and self.env.context.get('tecdoc_no_cache') is not True:
                cached = cache_model.get_cached(self, method, endpoint, params, body_for_cache, include_expired=True)
                if cached is not None:
                    _logger.warning('TecDoc API failed; returning cached (possibly stale) response for %s', endpoint)
                    return cached

            raise UserError(f"TecDoc API Error: {error_text}")

    def _fetch_image_base64(self, url):
        """Download an image URL and return base64-encoded string for Odoo (or False).

        Handles WebP conversion to PNG for Odoo compatibility.
        Returns ASCII string (not bytes) as required by Odoo's image fields.
        """
        self.ensure_one()
        if not url:
            _logger.debug("No image URL provided, skipping image download")
            return False

        # Step 1: Download image with proper headers
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (compatible; OdooTecDocSync/1.0)',
                'Accept': 'image/webp,image/png,image/jpeg,image/*,*/*',
            }
            resp = requests.get(url, timeout=30, headers=headers, allow_redirects=True)
            resp.raise_for_status()
            content = resp.content or b""

            _logger.info(
                "TecDoc image downloaded: url=%s, status=%s, size=%s bytes, content-type=%s",
                url, resp.status_code, len(content), resp.headers.get('Content-Type', 'unknown')
            )

            if not content or len(content) < 100:
                _logger.warning("TecDoc image empty or too small (%s bytes): %s", len(content), url)
                return False

            # Safety guard: avoid storing very large images (8MB limit)
            if len(content) > 8 * 1024 * 1024:
                _logger.warning("TecDoc image too large (%s bytes), skipping: %s", len(content), url)
                return False

        except requests.RequestException as e:
            _logger.warning("Failed to download TecDoc image %s: %s", url, e)
            return False

        # Step 2: Process image with Pillow (convert WebP to PNG for Odoo compatibility)
        if PIL_AVAILABLE:
            try:
                # Check WebP support
                webp_supported = pil_features.check('webp') if pil_features else False
                _logger.debug("Pillow WebP support: %s", webp_supported)

                # Open image with Pillow
                img = Image.open(BytesIO(content))
                original_format = img.format
                _logger.info("Image opened: format=%s, mode=%s, size=%s", img.format, img.mode, img.size)

                # Convert to RGB if necessary (handles RGBA, P, LA modes)
                if img.mode in ('RGBA', 'LA', 'P'):
                    # Create white background for transparency
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    if img.mode in ('RGBA', 'LA'):
                        # Paste with alpha mask
                        alpha = img.split()[-1] if img.mode == 'RGBA' else img.split()[1]
                        background.paste(img, mask=alpha)
                    else:
                        background.paste(img)
                    img = background
                elif img.mode != 'RGB':
                    img = img.convert('RGB')

                # Save as PNG to buffer (most compatible format for Odoo)
                buffer = BytesIO()
                img.save(buffer, format='PNG', optimize=True)
                image_data = buffer.getvalue()

                _logger.info(
                    "Image converted: %s -> PNG, original_size=%s bytes, converted_size=%s bytes",
                    original_format, len(content), len(image_data)
                )

            except Exception as e:
                _logger.warning(
                    "Pillow processing failed for %s (%s), using raw bytes: %s",
                    url, type(e).__name__, e
                )
                # Fallback: use raw bytes if Pillow fails
                image_data = content
        else:
            _logger.warning("Pillow not available, using raw image bytes (WebP may not display)")
            image_data = content

        # Step 3: Base64 encode and return as ASCII string (required by Odoo)
        try:
            image_b64 = base64.b64encode(image_data).decode('ascii')
            _logger.info(
                "Image base64 encoded: length=%s chars (first 50: %s...)",
                len(image_b64), image_b64[:50]
            )
            return image_b64
        except Exception as e:
            _logger.error("Failed to base64 encode image: %s", e)
            return False

    @staticmethod
    def _is_not_found_error(error):
        msg = str(error or '')
        return '404 Client Error' in msg or msg.strip().startswith('404 ')

    @api.model
    def _product_vals_from_article_snippet(self, article, fallback_article_no=None):
        if not isinstance(article, dict):
            article = {}

        article_no = article.get('articleNo') or article.get('article_no') or fallback_article_no
        supplier_id = article.get('supplierId') or article.get('supplier_id')
        article_id = article.get('articleId') or article.get('article_id')
        name = (
            article.get('articleName')
            or article.get('article_name')
            or article.get('articleProductName')
            or article.get('article_product_name')
            or article.get('genericArticleName')
            or article.get('generic_article_name')
            or article_no
            or 'Unknown'
        )

        vals = {
            'name': name,
            'tecdoc_article_no': article_no,
            'tecdoc_supplier_id': supplier_id,
            'tecdoc_supplier_name': article.get('supplierName') or article.get('supplier_name') or article.get('brandName') or article.get('brand_name'),
            'tecdoc_image_url': article.get('s3image') or article.get('imageUrl') or article.get('image_url'),
            'tecdoc_media_filename': article.get('articleMediaFileName') or article.get('mediaFileName'),
            'tecdoc_media_type': article.get('articleMediaType') or article.get('mediaType'),
            # Keep internal reference close to what the user typed (usually without spaces).
            'default_code': fallback_article_no or (article_no or ''),
            'list_price': 0.0,
            # Odoo 18: product.template.type is ('consu','service','combo').
            # Storable products are controlled by stock's `is_storable`.
            'type': 'consu',
            'is_storable': True,
        }
        if article_id is not None:
            vals['tecdoc_id'] = str(article_id)
        return vals

    @api.model
    def sync_product_from_article_snippet(self, article, fallback_article_no=None):
        """Create/update product from a search result when details endpoints are unavailable."""
        create_vals = self._product_vals_from_article_snippet(article, fallback_article_no=fallback_article_no)
        if not create_vals.get('tecdoc_article_no') and not create_vals.get('tecdoc_id'):
            raise UserError("TecDoc: could not create product (missing article number and id).")

        Product = self.env['product.product']
        domain = []
        if create_vals.get('tecdoc_id'):
            domain = [('tecdoc_id', '=', create_vals['tecdoc_id'])]
        elif create_vals.get('tecdoc_article_no'):
            domain = [('tecdoc_article_no', '=', create_vals['tecdoc_article_no'])]

        product = Product.search(domain, limit=1) if domain else Product.browse()
        if product:
            # Do not force stock tracking/type on existing products; keep operator choice.
            update_vals = dict(create_vals)
            update_vals.pop('type', None)
            update_vals.pop('is_storable', None)
            product.write(update_vals)
        else:
            product = Product.create(create_vals)
        template = product.product_tmpl_id if product._name == 'product.product' else product
        image_url = create_vals.get('tecdoc_image_url')
        should_download = self.download_images and template and (self.overwrite_images or not template.image_1920)
        if should_download and image_url:
            image_b64 = self._fetch_image_base64(image_url)
            if image_b64:
                template.write({'image_1920': image_b64})
        return product

    @staticmethod
    def _extract_article(payload):
        if payload is None:
            return {}
        if isinstance(payload, list):
            return payload[0] if payload else {}
        if not isinstance(payload, dict):
            return {}

        for key in ('article', 'articleDetails', 'article_detail', 'article_data', 'data'):
            value = payload.get(key)
            if isinstance(value, dict) and value:
                return value
        articles = payload.get('articles')
        if isinstance(articles, list) and articles:
            first = articles[0]
            return first if isinstance(first, dict) else {}
        return payload

    @staticmethod
    def _extract_articles(payload):
        if payload is None:
            return []
        if isinstance(payload, list):
            return [a for a in payload if isinstance(a, dict)]
        if not isinstance(payload, dict):
            return []
        articles = payload.get('articles')
        if isinstance(articles, list):
            return [a for a in articles if isinstance(a, dict)]
        data = payload.get('data')
        if isinstance(data, dict) and isinstance(data.get('articles'), list):
            return [a for a in data.get('articles') if isinstance(a, dict)]
        return []

    @staticmethod
    def _is_explicit_empty_article_response(payload):
        if not isinstance(payload, dict):
            return False

        if 'articles' in payload:
            articles = payload.get('articles')
            count = payload.get('countArticles')
            if articles is None:
                return count in (None, False, 0, '0')
            if isinstance(articles, list):
                return not any(isinstance(article, dict) for article in articles)

        data = payload.get('data')
        if isinstance(data, dict) and 'articles' in data:
            articles = data.get('articles')
            count = data.get('countArticles')
            if articles is None:
                return count in (None, False, 0, '0')
            if isinstance(articles, list):
                return not any(isinstance(article, dict) for article in articles)

        return False

    @staticmethod
    def _normalize_article_record(article):
        """Normalize TecDoc article dicts across provider variants."""
        if not isinstance(article, dict):
            return {}
        info = article.get('articleInfo')
        if isinstance(info, dict) and info:
            merged = dict(article)
            # Prefer explicit top-level values, then fill gaps from articleInfo.
            for key, value in info.items():
                merged.setdefault(key, value)
            return merged
        return article

    @staticmethod
    def _format_oem_numbers(oem_list):
        if not isinstance(oem_list, list):
            return False
        lines = []
        for item in oem_list:
            if not isinstance(item, dict):
                continue
            brand = (item.get('oemBrand') or item.get('brand') or '').strip()
            number = (item.get('oemDisplayNo') or item.get('oemNo') or item.get('number') or '').strip()
            if not number:
                continue
            lines.append(f"{brand + ': ' if brand else ''}{number}")
        return "\n".join(dict.fromkeys(lines)) if lines else False

    @staticmethod
    def _format_specifications(spec_list):
        if not isinstance(spec_list, list):
            return False
        lines = []
        for item in spec_list:
            if not isinstance(item, dict):
                continue
            name = (item.get('criteriaName') or item.get('name') or '').strip()
            value = (item.get('criteriaValue') or item.get('value') or '').strip()
            if not name and not value:
                continue
            if name and value:
                lines.append(f"{name}: {value}")
            else:
                lines.append(name or value)
        return "\n".join(lines) if lines else False

    @staticmethod
    def _format_compatible_cars(cars):
        if not isinstance(cars, list):
            return False
        lines = []
        for car in cars[:50]:
            if not isinstance(car, dict):
                continue
            manufacturer = car.get('manufacturerName') or car.get('manufacturer') or ''
            model = car.get('modelName') or car.get('model') or ''
            engine = car.get('typeEngineName') or car.get('engine') or ''
            start = car.get('constructionIntervalStart') or car.get('yearStart') or ''
            end = car.get('constructionIntervalEnd') or car.get('yearEnd') or ''
            base = " ".join([p for p in [manufacturer, model] if p]).strip()
            extra = " ".join([p for p in [engine] if p]).strip()
            interval = "–".join([p for p in [start, end] if p]).strip("–")
            parts = [p for p in [base, extra, f"({interval})" if interval else ""] if p]
            if parts:
                lines.append(" ".join(parts))
        return "\n".join(lines) if lines else False

    # ===== SEARCH FUNCTIONS =====

    def search_article_by_number(self, article_no):
        """Search article by article number"""
        return self.search_articles_by_article_no(article_no, article_type='ArticleNumber', lang_id=self.lang_id)

    def search_article_by_number_and_supplier(self, article_no, supplier_id):
        """Search article by article number and supplier id"""
        params = {
            'langId': self.lang_id,
            'articleNo': article_no,
            'articleType': 'ArticleNumber',
            'supplierId': supplier_id,
        }
        endpoint = "/artlookup/search-articles-by-article-no"
        try:
            return self._make_request(endpoint, params=params)
        except UserError:
            fallback_endpoints = [
                (
                    f"/artlookup/search-articles-by-article-no/lang-id/{self.lang_id}"
                    f"/article-type/ArticleNumber/article-no/{self._path(article_no)}"
                ),
                (
                    f"/articles/search-by-articles-no-supplier-id/lang-id/{self.lang_id}"
                    f"/supplier-id/{supplier_id}/article-no/{self._path(article_no)}"
                ),
                (
                    f"/articles/search/lang-id/{self.lang_id}/supplier-id/{supplier_id}"
                    f"/article-search/{self._path(article_no)}"
                ),
            ]
            last_error = None
            for fallback in fallback_endpoints:
                try:
                    return self._make_request(fallback)
                except UserError as err:
                    last_error = err
            raise last_error or UserError("TecDoc API Error: supplier article search failed.")

    def get_article_details(self, article_id):
        """Get complete article details (tries multiple provider variants)."""
        self.ensure_one()
        article_id_path = self._path(article_id)

        # Newer endpoints tend to include the explicit `article-id/{id}` segment.
        # Keep the older `article-id-details/{id}` variant as a fallback for backward compatibility.
        endpoints = [
            (
                f"/articles/article-complete-details/type-id/1/article-id/{article_id_path}"
                f"/lang-id/{self.lang_id}/country-filter-id/{self.country_filter_id}"
            ),
            (
                f"/articles/article-id-details/article-id/{article_id_path}"
                f"/lang-id/{self.lang_id}/country-filter-id/{self.country_filter_id}"
            ),
            (
                f"/articles/article-id-details/{article_id_path}"
                f"/lang-id/{self.lang_id}/country-filter-id/{self.country_filter_id}"
            ),
        ]

        last_error = None
        for idx, endpoint in enumerate(endpoints, start=1):
            try:
                data = self._make_request(endpoint)
                if idx > 1:
                    _logger.info(
                        "TecDoc: resolved articleId=%s via details fallback #%s (%s)",
                        article_id,
                        idx,
                        endpoint,
                    )
                return data
            except UserError as e:
                _logger.info(
                    "TecDoc: details attempt #%s failed for articleId=%s (%s): %s",
                    idx,
                    article_id,
                    endpoint,
                    e,
                )
                last_error = e
        raise last_error or UserError("TecDoc API Error: Could not fetch article details.")

    def get_article_details_by_number(self, article_no):
        """Get complete article details by article number"""
        endpoint = (
            f"/articles/article-number-details/lang-id/{self.lang_id}"
            f"/country-filter-id/{self.country_filter_id}/article-no/{self._path(article_no)}"
        )
        return self._make_request(endpoint)

    def get_article_details_by_number_typed(self, article_no, type_id=1, lang_id=None, country_filter_id=None):
        """Get article details by number (typed endpoint)"""
        lang_id = lang_id or self.lang_id
        country_filter_id = country_filter_id or self.country_filter_id
        endpoint = (
            f"/articles/article-number-details/type-id/{type_id}/lang-id/{lang_id}"
            f"/country-filter-id/{country_filter_id}/article-no/{self._path(article_no)}"
        )
        return self._make_request(endpoint)

    def post_article_details_by_number(self, payload):
        """POST article number details (payload as provided by RapidAPI docs)"""
        endpoint = "/articles/article-number-details"
        return self._make_request(endpoint, method='POST', json_data=payload)

    def post_article_details_by_number_form(self, article_no, type_id=1, lang_id=None, country_filter_id=None):
        """POST article number details using form-encoded payload."""
        lang_id = lang_id or self.lang_id
        country_filter_id = country_filter_id or self.country_filter_id
        endpoint = "/articles/article-number-details"
        form_payload = {
            'typeId': type_id,
            'langId': lang_id,
            'countryFilterId': country_filter_id,
            'articleNo': article_no,
        }
        return self._make_request(endpoint, method='POST', form_data=form_payload)

    def get_article_complete_details(self, article_id, type_id=1, lang_id=None, country_filter_id=None):
        """Get article details & compatibility for article id (complete details)"""
        lang_id = lang_id or self.lang_id
        country_filter_id = country_filter_id or self.country_filter_id
        endpoint = (
            f"/articles/article-complete-details/type-id/{type_id}/article-id/{article_id}"
            f"/lang-id/{lang_id}/country-filter-id/{country_filter_id}"
        )
        return self._make_request(endpoint)

    def get_article_details_by_id_lang(self, article_id, lang_id=None):
        """Get article details by article id and language id."""
        lang_id = lang_id or self.lang_id
        endpoint = f"/articles/details/article-id/{self._path(article_id)}/lang-id/{lang_id}"
        return self._make_request(endpoint)

    def post_article_id_complete_details(self, payload):
        """POST article id complete details (payload as provided by RapidAPI docs)"""
        endpoint = "/articles/article-id-complete-details"
        return self._make_request(endpoint, method='POST', json_data=payload)

    def post_article_details_by_article_id_lang(self, article_id, lang_id=None):
        """POST article details by article id and language id."""
        lang_id = lang_id or self.lang_id
        endpoint = "/articles/details"
        form_payload = {'articleId': article_id, 'langId': lang_id}
        return self._make_request(endpoint, method='POST', form_data=form_payload)

    def post_article_details(self, form_payload):
        """POST /articles/details (form payload)"""
        endpoint = "/articles/details"
        return self._make_request(endpoint, method='POST', form_data=form_payload)

    def get_article_specifications_criteria(self, article_id, lang_id=None, country_filter_id=None):
        """Get selection of all specifications criterias for the article"""
        lang_id = lang_id or self.lang_id
        country_filter_id = country_filter_id or self.country_filter_id
        endpoint = (
            f"/articles/selection-of-all-specifications-criterias-for-the-article/article-id/{article_id}"
            f"/lang-id/{lang_id}/country-filter-id/{country_filter_id}"
        )
        return self._make_request(endpoint)

    def get_article_specifications_by_article_ids(self, article_ids, lang_id=None):
        """Get specifications by article ids list."""
        lang_id = lang_id or self.lang_id
        ids = ','.join(str(article_id) for article_id in (article_ids if isinstance(article_ids, (list, tuple, set)) else [article_ids]))
        endpoint = f"/articles/get-article-specifications-list-of-articles-ids/lang-id/{lang_id}/articles-ids/{self._path(ids)}"
        return self._make_request(endpoint)

    def post_article_specifications_by_article_ids(self, article_ids, lang_id=None):
        """POST specifications by article ids list."""
        lang_id = lang_id or self.lang_id
        endpoint = "/articles/get-article-specifications-list-of-articles-ids"
        form_payload = {'langId': lang_id, 'articlesIds': article_ids}
        return self._make_request(endpoint, method='POST', form_data=form_payload)

    def get_oems_by_article_ids(self, article_ids, lang_id=None):
        """Get OEM numbers by article ids list."""
        lang_id = lang_id or self.lang_id
        ids = ','.join(str(article_id) for article_id in (article_ids if isinstance(article_ids, (list, tuple, set)) else [article_ids]))
        endpoint = f"/articles/get-oems-by-list-of-articles-ids/lang-id/{lang_id}/articles-ids/{self._path(ids)}"
        return self._make_request(endpoint)

    def list_articles_by_vehicle_and_category_typed(self, vehicle_id, category_id, type_id=1, lang_id=None):
        """List articles by vehicle id and category id (typed endpoint)"""
        lang_id = lang_id or self.lang_id
        endpoint = f"/articles/list/type-id/{type_id}/vehicle-id/{vehicle_id}/category-id/{category_id}/lang-id/{lang_id}"
        return self._make_request(endpoint)

    def post_list_articles(self, form_payload):
        """POST /articles/list-articles (form payload)"""
        endpoint = "/articles/list-articles"
        return self._make_request(endpoint, method='POST', form_data=form_payload)

    def get_compatible_cars_by_article_number(self, article_no, type_id=1, lang_id=None, country_filter_id=None, supplier_id=None):
        """Get compatible cars by article number (typed endpoint)"""
        lang_id = lang_id or self.lang_id
        country_filter_id = country_filter_id or self.country_filter_id
        if supplier_id is None:
            endpoint = (
                f"/articles/get-compatible-cars-by-article-number/type-id/{type_id}/article-no/{article_no}"
                f"/lang-id/{lang_id}/country-filter-id/{country_filter_id}"
            )
        else:
            endpoint = (
                f"/articles/get-compatible-cars-by-article-number/type-id/{type_id}/article-no/{article_no}"
                f"/supplier-id/{supplier_id}/lang-id/{lang_id}/country-filter-id/{country_filter_id}"
            )
        return self._make_request(endpoint)

    def post_compatible_cars_by_article_number(self, payload):
        """POST /articles/get-compatible-cars-by-article-number (json payload)"""
        endpoint = "/articles/get-compatible-cars-by-article-number"
        return self._make_request(endpoint, method='POST', json_data=payload)

    def post_compatible_cars_by_article_number_form(self, form_payload):
        """POST compatible cars by article number using form payload."""
        endpoint = "/articles/get-compatible-cars-by-article-number"
        return self._make_request(endpoint, method='POST', form_data=form_payload)

    def search_articles_by_oem(self, oem_number):
        """Search articles by OEM number"""
        endpoint = f"/articles/search-by-oem/lang-id/{self.lang_id}/oem/{oem_number}"
        return self._make_request(endpoint)

    # ===== LANGUAGE & COUNTRY FUNCTIONS =====

    def list_languages(self):
        """Get all languages"""
        endpoint = "/languages/list"
        return self._make_request(endpoint)

    def get_language(self, lang_id):
        """Get language details by language id"""
        endpoint = f"/languages/get-language/lang-id/{lang_id}"
        return self._make_request(endpoint)

    def list_countries(self):
        """Get all countries"""
        endpoint = "/countries/list"
        return self._make_request(endpoint)

    def list_countries_by_lang_id(self, lang_id=None):
        """Get all countries by language id"""
        lang_id = lang_id or self.lang_id
        endpoint = f"/countries/list-countries-by-lang-id/{lang_id}"
        return self._make_request(endpoint)

    def get_country(self, country_filter_id=None, lang_id=None):
        """Get country details by language id and country filter id"""
        lang_id = lang_id or self.lang_id
        country_filter_id = country_filter_id or self.country_filter_id
        endpoint = f"/countries/get-country/lang-id/{lang_id}/country-filter-id/{country_filter_id}"
        return self._make_request(endpoint)

    # ===== VEHICLE FUNCTIONS =====

    def list_vehicle_types(self):
        """List all vehicle types"""
        endpoint = "/types/list-vehicles-type"
        return self._make_request(endpoint)

    def list_type_ids(self):
        """List vehicle type ids."""
        return self.list_vehicle_types()

    def get_vehicles_by_manufacturer(self, manufacturer_id, type_id=1):
        """Get vehicles by manufacturer"""
        endpoint = f"/models/list/manufacturer-id/{manufacturer_id}/lang-id/{self.lang_id}/country-filter-id/{self.country_filter_id}/type-id/{type_id}"
        return self._make_request(endpoint)

    def get_models_by_type_and_manufacturer(self, type_id, manufacturer_id, lang_id=None, country_filter_id=None):
        """Get models list by type id and manufacturer id"""
        lang_id = lang_id or self.lang_id
        country_filter_id = country_filter_id or self.country_filter_id
        endpoint = (
            f"/models/list/type-id/{type_id}/manufacturer-id/{manufacturer_id}"
            f"/lang-id/{lang_id}/country-filter-id/{country_filter_id}"
        )
        return self._make_request(endpoint)

    def get_vehicle_details(self, vehicle_id):
        """Get vehicle details"""
        endpoint = f"/models/get-model-details-by-vehicle-id/{vehicle_id}/lang-id/{self.lang_id}/country-filter-id/{self.country_filter_id}/type-id/1"
        return self._make_request(endpoint)

    def get_model_details_by_vehicle(self, vehicle_id, type_id=1, lang_id=None, country_filter_id=None):
        """Get model details by vehicle id (alternative endpoint shape)"""
        lang_id = lang_id or self.lang_id
        country_filter_id = country_filter_id or self.country_filter_id
        endpoint = f"/models/type-id/{type_id}/vehicles/{vehicle_id}/lang-id/{lang_id}/country-filter-id/{country_filter_id}"
        return self._make_request(endpoint)

    def get_model_details_by_model(self, model_id, type_id=1):
        """Get model details by model id"""
        endpoint = f"/models/type-id/{type_id}/model-id/{model_id}"
        return self._make_request(endpoint)

    def get_model_details_by_model_lang(self, model_id, type_id=1, lang_id=None, country_filter_id=None):
        """Get model details by model id with language/country filter."""
        lang_id = lang_id or self.lang_id
        country_filter_id = country_filter_id or self.country_filter_id
        endpoint = f"/models/type-id/{type_id}/model-id/{model_id}/lang-id/{lang_id}/country-filter-id/{country_filter_id}"
        return self._make_request(endpoint)

    def get_model_image(self, model_id):
        """Get model image by model id."""
        endpoint = f"/models/model-image/model-id/{model_id}"
        return self._make_request(endpoint)

    def decode_vin(self, vin_number):
        """Decode VIN number"""
        endpoint = f"/vin/decoder-v3/{vin_number}"
        return self._make_request(endpoint)

    def decode_vin_v1(self, vin_number):
        """Decode VIN number (v1)"""
        endpoint = f"/vin/decoder-v1/{vin_number}"
        return self._make_request(endpoint)

    def decode_vin_v2(self, vin_number):
        """Decode VIN number (v2)"""
        endpoint = f"/vin/decoder-v2/{vin_number}"
        return self._make_request(endpoint)

    def vin_check(self, vin_number):
        """VIN check"""
        endpoint = f"/vin/tecdoc-vin-check/{vin_number}"
        return self._make_request(endpoint)

    def decode_vin_v5(self, vin_number):
        """Decode VIN number (v5)"""
        endpoint = f"/vin/decoder-v5/{vin_number}"
        return self._make_request(endpoint)

    # ===== CATEGORY FUNCTIONS =====

    def get_categories_by_vehicle(self, vehicle_id, manufacturer_id, variant=3):
        """Get product categories for a vehicle"""
        endpoint = f"/category/category-products-groups-variant-{variant}/{vehicle_id}/manufacturer-id/{manufacturer_id}/lang-id/{self.lang_id}/country-filter-id/{self.country_filter_id}/type-id/1"
        return self._make_request(endpoint)

    def list_category_tree_structure(self, type_id=1, lang_id=None):
        """List all categories (tree structure)"""
        lang_id = lang_id or self.lang_id
        endpoint = f"/category/type-id/{type_id}/list-category-tree-structure/lang-id/{lang_id}"
        return self._make_request(endpoint)

    def list_categories_by_vehicle_variant(self, vehicle_id, variant, type_id=1, lang_id=None):
        """List categories by vehicle id (variant 1/2/3)"""
        lang_id = lang_id or self.lang_id
        endpoint = f"/category/type-id/{type_id}/products-groups-variant-{variant}/{vehicle_id}/lang-id/{lang_id}"
        return self._make_request(endpoint)

    def search_categories_by_text(self, search_text, type_id=1, lang_id=None):
        """Search (sub)categories by text"""
        lang_id = lang_id or self.lang_id
        endpoint = (
            f"/category/search-for-the-commodity-group-tree-by-description/type-id/{type_id}"
            f"/lang-id/{lang_id}/search-text/{search_text}"
        )
        return self._make_request(endpoint)

    def list_product_names(self, lang_id=None):
        """List all product names"""
        lang_id = lang_id or self.lang_id
        endpoint = f"/category/list-products-names/lang-id/{lang_id}"
        return self._make_request(endpoint)

    def get_articles_by_vehicle_and_category(self, vehicle_id, category_id):
        """Get articles by vehicle and category"""
        endpoint = f"/articles/list-by-vehicle-category/vehicle-id/{vehicle_id}/category-id/{category_id}/lang-id/{self.lang_id}"
        return self._make_request(endpoint)

    # ===== MANUFACTURER & SUPPLIER FUNCTIONS =====

    def get_all_manufacturers(self, type_id=1):
        """Get all manufacturers"""
        endpoint = f"/manufacturers/list-by-type-id/{type_id}"
        return self._make_request(endpoint)

    def get_manufacturer_ids_by_type_id(self, type_id=1):
        """Get manufacturer ids by type id (alternative endpoint shape)"""
        endpoint = f"/manufacturers/list/type-id/{type_id}"
        return self._make_request(endpoint)

    def get_manufacturer_details(self, manufacturer_id):
        """Get manufacturer details"""
        endpoint = f"/manufacturers/find-by-id/{manufacturer_id}"
        return self._make_request(endpoint)

    def get_all_suppliers(self):
        """Get all suppliers"""
        endpoint = "/suppliers/list"
        return self._make_request(endpoint)

    @api.model
    def _extract_supplier_rows(self, payload):
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if not isinstance(payload, dict):
            return []

        for key in ('suppliers', 'items', 'result'):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]

        data = payload.get('data')
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict):
            for key in ('suppliers', 'items', 'result'):
                value = data.get(key)
                if isinstance(value, list):
                    return [row for row in value if isinstance(row, dict)]

        return []

    def sync_suppliers_catalog(self, deactivate_missing=False, use_cache=False):
        self.ensure_one()
        api = self.with_context(tecdoc_no_cache=(not use_cache))
        payload = api.get_all_suppliers()
        rows = self._extract_supplier_rows(payload)
        if not rows:
            raise UserError("TecDoc suppliers response is empty or unsupported.")

        Supplier = self.env['tecdoc.supplier'].sudo()
        existing = Supplier.search([])
        by_supplier_id = {rec.supplier_id: rec for rec in existing}

        created = 0
        updated = 0
        seen = set()
        for row in rows:
            supplier_id = row.get('supplierId')
            if supplier_id is None:
                supplier_id = row.get('supplier_id')
            try:
                supplier_id = int(supplier_id)
            except Exception:
                continue
            if supplier_id <= 0:
                continue

            name = (row.get('supplierName') or row.get('supplier_name') or '').strip()
            if not name:
                continue

            vals = {
                'name': name,
                'supplier_match_code': (row.get('supplierMatchCode') or row.get('supplier_match_code') or '').strip() or False,
                'supplier_logo_name': (row.get('supplierLogoName') or row.get('supplier_logo_name') or '').strip() or False,
                'active': True,
            }
            seen.add(supplier_id)
            rec = by_supplier_id.get(supplier_id)
            if rec:
                changes = {}
                for key, value in vals.items():
                    if (rec[key] or False) != (value or False):
                        changes[key] = value
                if changes:
                    rec.write(changes)
                    updated += 1
            else:
                Supplier.create(dict(vals, supplier_id=supplier_id))
                created += 1

        deactivated = 0
        if deactivate_missing:
            to_deactivate = existing.filtered(lambda rec: rec.supplier_id > 0 and rec.supplier_id not in seen and rec.active)
            deactivated = len(to_deactivate)
            if to_deactivate:
                to_deactivate.write({'active': False})

        return {
            'total_received': len(rows),
            'created': created,
            'updated': updated,
            'deactivated': deactivated,
            'active_total': Supplier.search_count([('active', '=', True)]),
        }

    def action_sync_suppliers_catalog(self):
        self.ensure_one()
        result = self.sync_suppliers_catalog(deactivate_missing=False, use_cache=False)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'TecDoc Suppliers',
                'message': (
                    f"Synced suppliers: received={result['total_received']}, "
                    f"created={result['created']}, updated={result['updated']}."
                ),
                'type': 'success',
                'sticky': False,
            },
        }

    # ===== COMPATIBILITY FUNCTIONS =====

    def get_compatible_vehicles(self, article_no, supplier_id=None):
        """Get compatible vehicles for article"""
        if supplier_id:
            endpoint = f"/articles/compatible-vehicles/lang-id/{self.lang_id}/article-no/{article_no}/supplier-id/{supplier_id}"
        else:
            endpoint = f"/articles/compatible-vehicles/lang-id/{self.lang_id}/article-no/{article_no}"
        return self._make_request(endpoint)

    def get_article_media(self, article_id):
        """Get article media (images, etc.)"""
        endpoint = f"/articles/article-all-media-info/{article_id}/lang-id/{self.lang_id}"
        return self._make_request(endpoint)

    # ===== OEM & ANALOG LOOKUPS =====

    def list_vehicles_by_oem(self, manufacturer_id, article_oem_no, type_id=1, lang_id=None, country_filter_id=None):
        """List vehicles by OEM part number"""
        lang_id = lang_id or self.lang_id
        country_filter_id = country_filter_id or self.country_filter_id
        endpoint = (
            f"/articles-oem/selecting-a-list-of-cars-for-oem-part-number/type-id/{type_id}"
            f"/lang-id/{lang_id}/country-filter-id/{country_filter_id}/manufacturer-id/{manufacturer_id}"
            f"/article-oem-no/{article_oem_no}"
        )
        return self._make_request(endpoint)

    def analog_spare_parts_by_article_number(self, article_no, lang_id=None):
        """Analog spare parts by article number"""
        lang_id = lang_id or self.lang_id
        endpoint = f"/artlookup/search-for-analog-spare-parts-by-the-articles-numbers/lang-id/{lang_id}/articleNo/{article_no}"
        return self._make_request(endpoint)

    def analog_spare_parts_by_oem_number(self, article_oem_no):
        """Analog spare parts by OEM number"""
        endpoint = f"/artlookup/search-for-analogue-of-spare-parts-by-oem-number/article-oem-no/{article_oem_no}"
        return self._make_request(endpoint)

    # ===== ARTICLES: ACCESSORIES / PARTS / MEDIA / CATEGORIES =====

    def list_accessory_parts_by_article_id(self, article_id, lang_id=None, country_filter_id=None):
        """List accessory parts by article id"""
        lang_id = lang_id or self.lang_id
        country_filter_id = country_filter_id or self.country_filter_id
        endpoint = (
            f"/articles/selecting-list-of-accessories-list-for-the-article/article-id/{article_id}"
            f"/lang-id/{lang_id}/country-filter-id/{country_filter_id}"
        )
        return self._make_request(endpoint)

    def list_of_parts_for_article(self, article_id, lang_id=None, country_filter_id=None):
        """List of parts for article id"""
        lang_id = lang_id or self.lang_id
        country_filter_id = country_filter_id or self.country_filter_id
        endpoint = f"/articles/list-of-parts-for-article/article-id/{article_id}/lang-id/{lang_id}/country-filter-id/{country_filter_id}"
        return self._make_request(endpoint)

    def parts_diagram_coordinates(self, article_id):
        """Parts diagram coordinates for the parts list (by article id)"""
        endpoint = f"/articles/selecting-item-coordinators-on-the-parts-diagram-image-for-the-parts-list/article-id/{article_id}"
        return self._make_request(endpoint)

    def get_article_media_by_article_id(self, article_id, lang_id=None):
        """Get article media (alternate endpoint shape)"""
        lang_id = lang_id or self.lang_id
        endpoint = f"/articles/article-all-media-info/article-id/{article_id}/lang-id/{lang_id}"
        return self._make_request(endpoint)

    def post_article_media(self, form_payload):
        """POST article media (form payload)"""
        endpoint = "/articles/article-all-media-info"
        return self._make_request(endpoint, method='POST', form_data=form_payload)

    def get_categories_by_article_id(self, article_id, lang_id=None):
        """Get categories by article id"""
        lang_id = lang_id or self.lang_id
        endpoint = f"/articles/get-article-category/article-id/{article_id}/lang-id/{lang_id}"
        return self._make_request(endpoint)

    def list_categories_by_article_id(self, article_id, lang_id=None):
        """List categories by article id (plural endpoint shape)."""
        lang_id = lang_id or self.lang_id
        endpoint = f"/articles/get-article-categories/article-id/{article_id}/lang-id/{lang_id}"
        return self._make_request(endpoint)

    def post_quick_article_search(self, form_payload):
        """POST quick article search (form payload)"""
        endpoint = "/articles/quick-article-search"
        return self._make_request(endpoint, method='POST', form_data=form_payload)

    def search_by_article_no_and_supplier_id(self, article_no, supplier_id, lang_id=None):
        """Search articles by article no & supplier id (alt endpoint)"""
        lang_id = lang_id or self.lang_id
        endpoint = f"/articles/search-by-articles-no-supplier-id/lang-id/{lang_id}/supplier-id/{supplier_id}/article-no/{article_no}"
        return self._make_request(endpoint)

    def post_search_articles_by_number(self, article_no, supplier_id=None, article_type='ArticleNumber', lang_id=None):
        """POST search articles by article number and optional supplier id."""
        lang_id = lang_id or self.lang_id
        endpoint = "/artlookup/search-articles-by-article-no"
        form_payload = {'langId': lang_id, 'articleNo': article_no, 'articleType': article_type}
        if supplier_id:
            form_payload['supplierId'] = supplier_id
        return self._make_request(endpoint, method='POST', form_data=form_payload)

    def search_oem_by_article_oem_no(self, article_oem_no, lang_id=None):
        """Search articles by OEM number (alt endpoint)"""
        lang_id = lang_id or self.lang_id
        endpoint = f"/articles-oem/search-by-article-oem-no/lang-id/{lang_id}/article-oem-no/{article_oem_no}"
        return self._make_request(endpoint)

    def post_article_oem_search_no(self, form_payload):
        """POST OEM article search (form payload)"""
        endpoint = "/articles-oem/article-oem-search-no"
        return self._make_request(endpoint, method='POST', form_data=form_payload)

    def get_oem_by_article_id(self, article_id, lang_id=None):
        """Get OEM numbers by article id."""
        lang_id = lang_id or self.lang_id
        endpoint = f"/articles-oem/get-oem-by-article-id/article-id/{self._path(article_id)}/lang-id/{lang_id}"
        return self._make_request(endpoint)

    def post_get_oem_by_article_id(self, article_id, lang_id=None):
        """POST OEM lookup by article id."""
        lang_id = lang_id or self.lang_id
        endpoint = "/articles-oem/get-oem-by-article-id"
        return self._make_request(endpoint, method='POST', form_data={'articleId': article_id, 'langId': lang_id})

    # ===== CROSS REFERENCES / OEM EQUIVALENTS =====

    def cross_references_through_oem_numbers(self, article_no, supplier_name):
        """Cross-references through OEM numbers (by article no + supplier name)"""
        endpoint = f"/artlookup/search-for-cross-references-through-oem-numbers/article-no/{article_no}/supplierName/{supplier_name}"
        return self._make_request(endpoint)

    def cross_references_through_oem_numbers_supplier_id(self, article_no, supplier_id):
        """Cross-references through OEM numbers (by article no + supplier id)."""
        endpoint = (
            f"/artlookup/search-for-cross-references-through-oem-numbers/"
            f"article-no/{self._path(article_no)}/supplierId/{self._path(supplier_id)}"
        )
        return self._make_request(endpoint)

    def post_cross_references_through_oem_numbers(self, form_payload):
        """POST search for cross-references through OEM numbers."""
        endpoint = "/artlookup/search-for-cross-references-through-oem-numbers"
        return self._make_request(endpoint, method='POST', form_data=form_payload)

    def cross_references_through_oem_numbers_by_article_id(self, article_id, lang_id=None):
        """Cross-references through OEM numbers by article id."""
        lang_id = lang_id or self.lang_id
        endpoint = (
            f"/artlookup/search-for-cross-references-through-oem-numbers-by-article-id/"
            f"article-id/{self._path(article_id)}/lang-id/{lang_id}"
        )
        return self._make_request(endpoint)

    def cross_references_by_article_id(self, article_id, lang_id=None):
        """Cross-references by article id"""
        lang_id = lang_id or self.lang_id
        endpoint = f"/artlookup/select-article-cross-references/article-id/{article_id}/lang-id/{lang_id}"
        return self._make_request(endpoint)

    def cross_references_by_article_id_partial_match(self, article_id, lang_id=None):
        """Cross-references by article id, partial-match variant."""
        lang_id = lang_id or self.lang_id
        endpoint = f"/artlookup/select-article-cross-references-partial-match/article-id/{article_id}/lang-id/{lang_id}"
        return self._make_request(endpoint)

    def oem_oem_cross_reference_through_aftermarket(self, article_oem_no):
        """OEM/OEM cross-reference through aftermarket parts references"""
        endpoint = f"/artlookup/search-for-the-oem-cross-references-through-aftermarket-parts-references/article-oem-no/{article_oem_no}"
        return self._make_request(endpoint)

    def equivalent_oem_numbers(self, article_oem_no, lang_id=None):
        """Equivalent OEM numbers (GET)"""
        lang_id = lang_id or self.lang_id
        endpoint = f"/articles-oem/search-all-equal-oem-no/lang-id/{lang_id}/article-oem-no/{article_oem_no}"
        return self._make_request(endpoint)

    def post_equivalent_oem_numbers(self, article_oem_search_no, lang_id=None):
        """Equivalent OEM numbers (POST with query params)"""
        lang_id = lang_id or self.lang_id
        endpoint = "/articles-oem/all-equal-oem-no"
        params = {'langId': lang_id, 'articleOemSearchNo': article_oem_search_no}
        return self._make_request(endpoint, method='POST', params=params, form_data={})

    def parts_cross_reference_by_article_no(self, article_no, article_type='IAMNumber', lang_id=None):
        """Parts cross-reference by article no"""
        lang_id = lang_id or self.lang_id
        endpoint = f"/artlookup/search-for-cross-numbers/lang-id/{lang_id}/article-type/{self._path(article_type)}/article-no/{self._path(article_no)}"
        return self._make_request(endpoint)

    def post_search_for_cross_numbers(self, form_payload):
        """POST search for cross-numbers."""
        endpoint = "/artlookup/search-for-cross-numbers"
        return self._make_request(endpoint, method='POST', form_data=form_payload)

    def search_articles_by_article_no(self, article_no, article_type='ArticleNumber', lang_id=None):
        """Search articles by article no (artlookup endpoint)"""
        lang_id = lang_id or self.lang_id
        params = {
            'langId': lang_id,
            'articleNo': article_no,
            'articleType': article_type,
        }
        endpoint = "/artlookup/search-articles-by-article-no"
        try:
            return self._make_request(endpoint, params=params)
        except UserError:
            fallback_endpoints = [
                (
                    f"/artlookup/search-articles-by-article-no/lang-id/{lang_id}"
                    f"/article-type/{self._path(article_type)}/article-no/{self._path(article_no)}"
                ),
                f"/articles/search-by-article-no/lang-id/{lang_id}/article-no/{self._path(article_no)}",
                f"/articles/search/lang-id/{lang_id}/article-search/{self._path(article_no)}",
            ]
            last_error = None
            for fallback in fallback_endpoints:
                try:
                    return self._make_request(fallback)
                except UserError as err:
                    last_error = err
            raise last_error or UserError("TecDoc API Error: article search failed.")

    def search_articles_by_ean(self, ean, lang_id=None):
        """Best-effort search by EAN/barcode.

        RapidAPI TecDoc does not always expose a dedicated 'EAN search' in the UI docs.
        Many provider variants accept EAN in the standard article search endpoints, so we try:
        1) artlookup with article-type=EAN
        2) artlookup with article-type=EANNumber (older provider variant)
        3) standard search-by-article-no (treating EAN as an article number)
        """
        lang_id = lang_id or self.lang_id
        ean = (ean or '').strip()
        if not ean:
            return {}
        try:
            return self.search_articles_by_article_no(ean, article_type='EAN', lang_id=lang_id)
        except UserError:
            try:
                return self.search_articles_by_article_no(ean, article_type='EANNumber', lang_id=lang_id)
            except UserError:
                return self.search_article_by_number(ean)

    def search_articles_by_oem_no(self, oem_no, lang_id=None):
        """Search by OEM number (wrapper around the provider's OEM endpoints)."""
        lang_id = lang_id or self.lang_id
        oem_no = (oem_no or '').strip()
        if not oem_no:
            return {}
        try:
            return self.search_articles_by_article_no(oem_no, article_type='OENumber', lang_id=lang_id)
        except UserError:
            try:
                return self.search_oem_by_article_oem_no(oem_no, lang_id=lang_id)
            except UserError:
                return self.search_articles_by_oem(oem_no)

    def search_articles_by_iam_no(self, iam_no, lang_id=None):
        """Search by IAM number."""
        return self.search_articles_by_article_no(iam_no, article_type='IAMNumber', lang_id=lang_id or self.lang_id)

    def search_articles_by_equivalent_no(self, article_no, lang_id=None):
        """Search equivalent/cross article references by IAM/cross number."""
        lang_id = lang_id or self.lang_id
        try:
            return self.search_articles_by_iam_no(article_no, lang_id=lang_id)
        except UserError:
            return self.parts_cross_reference_by_article_no(article_no, article_type='IAMNumber', lang_id=lang_id)

    def search_articles_by_trade_no(self, trade_no, lang_id=None):
        """Search by trade number."""
        return self.search_articles_by_article_no(trade_no, article_type='TradeNumber', lang_id=lang_id or self.lang_id)

    # ===== VEHICLES: SPARE PART CRITERIA =====

    def vehicle_spare_part_criteria(self, vehicle_id, type_id=1, lang_id=None, country_filter_id=None):
        """Vehicle spare part criteria (OLAP query)"""
        lang_id = lang_id or self.lang_id
        country_filter_id = country_filter_id or self.country_filter_id
        endpoint = (
            "/types/selecting-all-criteria-for-spare-parts-of-a-passenger-car-using-an-olap-query"
            f"/type-id/{type_id}/lang-id/{lang_id}/country-filter-id/{country_filter_id}/vehicle-id/{vehicle_id}"
        )
        return self._make_request(endpoint)

    # ===== ODOO INTEGRATION HELPERS =====

    # ===== TYPES / ENGINE DATA =====

    def get_vehicle_type_details(self, vehicle_id, manufacturer_id=None, type_id=1, lang_id=None, country_filter_id=None):
        """Get vehicle type detailed information"""
        lang_id = lang_id or self.lang_id
        country_filter_id = country_filter_id or self.country_filter_id
        if manufacturer_id is not None:
            endpoint = (
                f"/types/type-id/{type_id}/vehicle-type-details/{vehicle_id}"
                f"/manufacturer-id/{manufacturer_id}/lang-id/{lang_id}/country-filter-id/{country_filter_id}"
            )
        else:
            endpoint = f"/types/type-id/{type_id}/vehicle-type-details/{vehicle_id}/lang-id/{lang_id}/country-filter-id/{country_filter_id}"
        return self._make_request(endpoint)

    def list_engine_types_by_model(self, model_series_id, type_id=1, lang_id=None, country_filter_id=None):
        """List engine/vehicle types by model series id"""
        lang_id = lang_id or self.lang_id
        country_filter_id = country_filter_id or self.country_filter_id
        endpoint = f"/types/type-id/{type_id}/list-vehicles-types/{model_series_id}/lang-id/{lang_id}/country-filter-id/{country_filter_id}"
        return self._make_request(endpoint)

    def get_engine_details(self, engine_id, lang_id=None):
        """Get engine details"""
        lang_id = lang_id or self.lang_id
        endpoint = f"/engines/engine-details/engine-id/{engine_id}/lang-id/{lang_id}"
        return self._make_request(endpoint)

    def list_vehicle_ids_by_model_ids(self, model_series_id, type_id=1, lang_id=None, country_filter_id=None):
        """List vehicle ids by model series id"""
        lang_id = lang_id or self.lang_id
        country_filter_id = country_filter_id or self.country_filter_id
        endpoint = f"/types/type-id/{type_id}/list-vehicles-id/{model_series_id}/lang-id/{lang_id}/country-filter-id/{country_filter_id}"
        return self._make_request(endpoint)

    def find_vehicle_by_ltn_number(self, ltn_number, number_type, lang_id=None, country_filter_id=None):
        """Find vehicle by LTN number (KBA/Germany or NL plates)"""
        lang_id = lang_id or self.lang_id
        country_filter_id = country_filter_id or self.country_filter_id
        endpoint = (
            f"/types/searching-the-passenger-car-by-ltn-number/lang-id/{lang_id}"
            f"/country-filter-id/{country_filter_id}/ltn-number/{ltn_number}/number-type/{number_type}"
        )
        return self._make_request(endpoint)

    def vehicles_by_kba_number(self, kba_number, lang_id=None, country_filter_id=None):
        """Find vehicles by Germany KBA number."""
        return self.find_vehicle_by_ltn_number(kba_number, 'KBA', lang_id=lang_id, country_filter_id=country_filter_id)

    def vehicles_by_kenteken(self, license_plate, lang_id=None, country_filter_id=None):
        """Find vehicles by Netherlands Kenteken license plate."""
        return self.find_vehicle_by_ltn_number(license_plate, 'Kenteken', lang_id=lang_id, country_filter_id=country_filter_id)

    def get_type_id_by_vehicle_and_manufacturer(self, vehicle_id, manufacturer_id, type_id=1):
        """Get typeId by vehicleId and manufacturerId."""
        endpoint = (
            f"/types/get-typeid-by-vehicleid/type-id/{type_id}"
            f"/vehicle-id/{self._path(vehicle_id)}/manufacturer-id/{self._path(manufacturer_id)}"
        )
        return self._make_request(endpoint)

    # ===== PART / VEHICLE CRITERIA =====

    def get_part_criteria_for_vehicle(self, type_id, product_id, vehicle_id, supplier_id, lang_id=None, country_filter_id=None):
        """Get selection of criteria for articles and vehicle"""
        lang_id = lang_id or self.lang_id
        country_filter_id = country_filter_id or self.country_filter_id
        endpoint = (
            f"/articles/selection-of-the-criteria-for-articles-and-vehicle/type-id/{type_id}"
            f"/product-id/{product_id}/vehicle-id/{vehicle_id}/supplier-id/{supplier_id}"
            f"/lang-id/{lang_id}/country-filter-id/{country_filter_id}"
        )
        return self._make_request(endpoint)

    @api.model
    def sync_product_from_tecdoc(self, article_id=None, article_no=None, supplier_id=None):
        """Sync a product from TecDoc to Odoo.

        Prefer syncing by `article_no` (stable user input), and fall back to `article_id` when available.
        """
        self.ensure_one()

        raw_details = None
        if article_no:
            single_attempt = bool(self.env.context.get('tecdoc_single_attempt'))
            try:
                raw_details = self.post_article_details_by_number_form(article_no)
            except UserError as e:
                raw_details = None
                last_error = e
                _logger.info("TecDoc: article-number-details POST failed for %s: %s", article_no, e)
                if single_attempt:
                    raise last_error
                try:
                    raw_details = self.get_article_details_by_number_typed(article_no)
                except UserError:
                    raw_details = None
                if raw_details is None:
                    try:
                        raw_details = self.get_article_details_by_number(article_no)
                    except UserError:
                        raw_details = None
                if raw_details is None:
                    try:
                        alt = self.search_articles_by_article_no(article_no, article_type='ArticleNumber')
                        candidates = self._extract_articles(alt)
                        if candidates:
                            article_id = candidates[0].get('articleId') or candidates[0].get('article_id') or article_id
                    except UserError:
                        pass
                if supplier_id:
                    try:
                        search = self.search_article_by_number_and_supplier(article_no, supplier_id)
                        candidates = self._extract_articles(search)
                        if candidates:
                            article_id = candidates[0].get('articleId') or candidates[0].get('article_id') or article_id
                    except UserError:
                        pass
                if not article_id:
                    raise last_error

        if raw_details is None and article_id:
            raw_details = self.get_article_details(article_id)

        if self._is_explicit_empty_article_response(raw_details):
            raise UserError(
                "Article not found in TecDoc. Verify the article number/ID and your Language/Country Filter IDs."
            )

        articles = self._extract_articles(raw_details)
        article_data = {}
        if articles:
            normalized = [self._normalize_article_record(a) for a in articles if isinstance(a, dict)]
            if supplier_id:
                article_data = next(
                    (
                        a for a in normalized
                        if (a.get('supplierId') or a.get('supplier_id')) == supplier_id
                    ),
                    {},
                )
                if not article_data:
                    raise UserError(
                        f"TecDoc returned {len(normalized)} match(es) for '{article_no}', but none for supplier_id={supplier_id}. "
                        "Use “Find Supplier IDs” to pick a valid supplier."
                    )
            elif len(normalized) == 1:
                article_data = normalized[0]
            else:
                options = []
                for a in normalized[:20]:
                    sid = a.get('supplierId') or a.get('supplier_id')
                    sname = a.get('supplierName') or a.get('supplier_name') or ''
                    aid = a.get('articleId') or a.get('article_id')
                    name = (
                        a.get('articleProductName')
                        or a.get('articleName')
                        or a.get('genericArticleName')
                        or ''
                    )
                    options.append(f"- supplierId={sid}, supplierName={sname}, articleId={aid}, name={name}")
                raise UserError(
                    "Multiple TecDoc matches found for this article number. "
                    "Please enter a Supplier ID (or click “Find Supplier IDs”).\n\n"
                    + "\n".join(options)
                )
        else:
            article_data = self._normalize_article_record(self._extract_article(raw_details))
        if not article_data:
            raise UserError(
                "Article not found in TecDoc. Verify the article number/ID and your Language/Country Filter IDs."
            )

        resolved_article_id = (
            article_data.get('articleId')
            or article_data.get('article_id')
            or article_id
        )

        # Create or update product template (Odoo's main product form is product.template).
        ProductTemplate = self.env['product.template']

        ean = False
        ean_payload = article_data.get('eanNo') or article_data.get('ean_no') or {}
        if isinstance(ean_payload, dict):
            ean = ean_payload.get('eanNumbers') or ean_payload.get('eanNumber') or ean_payload.get('ean') or False

        vals = {
            'name': (
                article_data.get('articleName')
                or article_data.get('articleProductName')
                or article_data.get('articleProductNameLong')
                or article_data.get('genericArticleName')
                or article_data.get('articleNo')
                or article_no
                or 'Unknown'
            ),
            'tecdoc_id': str(resolved_article_id) if resolved_article_id is not None else False,
            'tecdoc_article_no': article_data.get('articleNo'),
            'tecdoc_supplier_id': article_data.get('supplierId'),
            'tecdoc_supplier_name': article_data.get('supplierName'),
            'tecdoc_ean': ean,
            'tecdoc_oem_numbers': self._format_oem_numbers(article_data.get('oemNo') or article_data.get('oem_no')),
            'tecdoc_specifications': self._format_specifications(article_data.get('allSpecifications') or article_data.get('all_specifications')),
            'tecdoc_image_url': article_data.get('s3image') or article_data.get('imageUrl') or article_data.get('image_url'),
            'tecdoc_media_filename': article_data.get('articleMediaFileName') or article_data.get('mediaFileName'),
            'tecdoc_media_type': article_data.get('articleMediaType') or article_data.get('mediaType'),
            'default_code': article_no or article_data.get('articleNo'),
            'list_price': 0.0,  # You'll need to set pricing
            # Odoo 18: see comment in _product_vals_from_article_snippet.
            'type': 'consu',
            'is_storable': True,
        }

        template = (
            ProductTemplate.search([('tecdoc_id', '=', str(resolved_article_id))], limit=1)
            if resolved_article_id
            else ProductTemplate.browse()
        )

        if template:
            # Keep manual inventory settings if the template already exists.
            update_vals = dict(vals)
            update_vals.pop('type', None)
            update_vals.pop('is_storable', None)
            template.write(update_vals)
        else:
            template = ProductTemplate.create(vals)

        variant = template.product_variant_id

        # Fill standard barcode field from TecDoc EAN when available.
        if ean and not template.barcode:
            try:
                template.write({'barcode': ean})
            except Exception as ex:
                _logger.warning("Could not set barcode=%s for template %s: %s", ean, template.id, ex)

        # Keep an internal barcode for scanning in warehouse, if not already set.
        if article_no and variant and not getattr(variant, 'barcode_internal', False):
            try:
                variant.write({'barcode_internal': article_no})
            except Exception:
                pass

        # Optional: sync the main image into the standard Odoo image field.
        # Odoo will then display it in the product form header automatically.
        image_url = vals.get('tecdoc_image_url')
        should_download = self.download_images and (self.overwrite_images or not template.image_1920)
        _logger.info(
            "Image sync check: template=%s, download_images=%s, overwrite=%s, has_image=%s, image_url=%s, will_download=%s",
            template.id, self.download_images, self.overwrite_images, bool(template.image_1920), image_url, should_download
        )

        if should_download and image_url:
            image_b64 = self._fetch_image_base64(image_url)
            if image_b64:
                try:
                    template.write({'image_1920': image_b64})
                    # Invalidate cache and verify
                    template.invalidate_recordset(['image_1920'])
                    if template.image_1920:
                        _logger.info("SUCCESS: image_1920 saved for template %s", template.id)
                        # Log attachment info for debugging
                        attachment = self.env['ir.attachment'].search([
                            ('res_model', '=', 'product.template'),
                            ('res_field', '=', 'image_1920'),
                            ('res_id', '=', template.id),
                        ], limit=1)
                        if attachment:
                            _logger.info(
                                "Image attachment: id=%s, file_size=%s, store_fname=%s",
                                attachment.id, attachment.file_size, attachment.store_fname
                            )
                    else:
                        _logger.error("FAILED: image_1920 still empty after write for template %s", template.id)
                except Exception as e:
                    _logger.exception("Could not write image_1920 for template %s: %s", template.id, e)
            else:
                _logger.warning("Image download/processing returned False for template %s, url=%s", template.id, image_url)
        elif not image_url:
            _logger.debug("No image URL available for template %s", template.id)

        # Prefer compatibility data embedded in the details payload to avoid extra API calls.
        embedded_cars = article_data.get('compatibleCars') or article_data.get('compatible_cars')
        embedded_text = self._format_compatible_cars(embedded_cars)
        if embedded_text:
            template.write({'tecdoc_compatibility': embedded_text})
        else:
            self._sync_vehicle_compatibility(template, resolved_article_id)

        self._sync_fast_variant_data(template, article_data, tecdoc_payload=raw_details)

        return variant or template

    def _sync_fast_variant_data(self, template, article_data, tecdoc_payload=None):
        """Populate normalized TecDoc variant/OEM/spec/EAN/vehicle tables for a single article."""
        self.ensure_one()
        template = (template or self.env['product.template'])[:1]
        article_data = self._normalize_article_record(article_data or {})
        if not template or not article_data:
            return False

        article_id = article_data.get('articleId') or article_data.get('article_id')
        article_no = (article_data.get('articleNo') or template.tecdoc_article_no or template.default_code or '').strip()
        if not article_id or not article_no:
            return False

        if not template.tecdoc_article_no_key:
            template.write({'tecdoc_article_no_key': _normalize_key(article_no)})

        importer = self.env['tecdoc.fast.import.run'].new({
            'replace_variant_details': True,
            'mark_products_managed': True,
            'import_cross_references': True,
        })
        importer._upsert_variant(template, article_data, tecdoc_payload if isinstance(tecdoc_payload, dict) else {})

        try:
            template.write({
                'tecdoc_fast_managed': True,
                'tecdoc_fast_last_import_at': fields.Datetime.now(),
            })
        except Exception:
            _logger.info("TecDoc: could not mark template %s as fast-managed during single-article sync", template.id)
        return True

    def enrich_tecdoc_variant_reference(self, variant):
        """Fetch full details and lightweight cross-reference targets for a TecDoc variant."""
        self.ensure_one()
        variant = variant.exists()[:1]
        if not variant:
            return False

        article_id = variant.article_id if variant.article_id and variant.article_id > 0 else None
        raw_details = None
        if article_id:
            try:
                raw_details = self.get_article_details(article_id)
            except UserError:
                raw_details = None
        if raw_details is None and variant.article_no:
            raw_details = self.post_article_details_by_number_form(variant.article_no)

        article_data = {}
        articles = self._extract_articles(raw_details)
        if articles:
            normalized = [self._normalize_article_record(a) for a in articles if isinstance(a, dict)]
            if variant.supplier_external_id:
                article_data = next(
                    (
                        a for a in normalized
                        if (a.get('supplierId') or a.get('supplier_id')) == variant.supplier_external_id
                    ),
                    {},
                )
            article_data = article_data or normalized[0]
        else:
            article_data = self._normalize_article_record(self._extract_article(raw_details))

        importer = self.env['tecdoc.fast.import.run'].new({
            'replace_variant_details': True,
            'mark_products_managed': False,
            'import_cross_references': True,
        })
        product = variant.product_tmpl_id if variant.product_tmpl_id else self.env['product.template']
        importer._upsert_variant(product, article_data, raw_details if isinstance(raw_details, dict) else {})

        refreshed_article_id = article_data.get('articleId') or article_data.get('article_id') or variant.article_id
        refreshed = self.env['tecdoc.article.variant'].sudo().search([('article_id', '=', refreshed_article_id)], limit=1) or variant
        self._sync_article_relation_targets(refreshed)
        refreshed.write({'last_enriched_at': fields.Datetime.now()})
        return True

    def _sync_article_relation_targets(self, source_variant):
        self.ensure_one()
        source_variant = source_variant.exists()[:1]
        if not source_variant or not source_variant.article_id or source_variant.article_id < 1:
            return False

        payloads = []
        for fetcher in (self.cross_references_by_article_id_partial_match, self.cross_references_through_oem_numbers_by_article_id):
            try:
                payloads.append(fetcher(source_variant.article_id))
            except UserError:
                continue

        Variant = self.env['tecdoc.article.variant'].sudo()
        Relation = self.env['tecdoc.article.relation'].sudo()
        seen = set()
        for payload in payloads:
            for article in self._extract_articles(payload):
                target = Variant._upsert_light_reference(article)
                if not target or target == source_variant:
                    continue
                dedupe = (source_variant.id, target.id, 'cross_ref')
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                if not Relation.search([
                    ('source_variant_id', '=', source_variant.id),
                    ('target_variant_id', '=', target.id),
                    ('relation_type', '=', 'cross_ref'),
                ], limit=1):
                    Relation.create({
                        'source_variant_id': source_variant.id,
                        'target_variant_id': target.id,
                        'relation_type': 'cross_ref',
                        'source': 'rapidapi',
                        'confidence': 80.0,
                    })
        return True

    def _sync_vehicle_compatibility(self, product, article_id):
        """Sync vehicle compatibility data"""
        try:
            compatibility_data = self.get_compatible_vehicles(product.tecdoc_article_no, supplier_id=product.tecdoc_supplier_id or None)

            if compatibility_data:
                # Store as JSON or create related records
                compatibility_text = "\n".join([
                    f"{v.get('manufacturer')} {v.get('model')} ({v.get('year')})"
                    for v in compatibility_data.get('vehicles', [])[:10]  # Limit to 10
                ])
                product.write({'tecdoc_compatibility': compatibility_text})

        except Exception as e:
            _logger.warning(f"Could not sync compatibility for product {product.id}: {str(e)}")


class TecDocSync(models.TransientModel):
    """Wizard for syncing products from TecDoc"""
    _name = 'tecdoc.sync.wizard'
    _description = 'TecDoc Product Sync Wizard'

    lookup_type = fields.Selection(
        [
            ('article_no', 'Article Number'),
            ('oem', 'OEM Number'),
            ('ean', 'Barcode / EAN'),
        ],
        string='Lookup Type',
        default='article_no',
        required=True,
    )
    article_number = fields.Char('Search Value', required=True)
    supplier_id = fields.Integer(
        'Supplier ID',
        help="Optional TecDoc supplierId filter. Leave empty/0 to search across all suppliers.",
    )
    candidates_info = fields.Text('Matches', readonly=True)
    candidate_ids = fields.One2many('tecdoc.sync.wizard.candidate', 'wizard_id', string='Matches')
    invoice_ingest_line_id = fields.Many2one('invoice.ingest.job.line', string='Invoice Ingest Line')

    def _candidate_key(self, article):
        return (
            article.get('supplierId') or article.get('supplier_id') or 0,
            article.get('articleId') or article.get('article_id') or 0,
            article.get('articleNo') or article.get('article_no') or self.article_number or '',
        )

    def _candidate_values(self, article):
        self.ensure_one()
        supplier_id = article.get('supplierId') or article.get('supplier_id') or 0
        article_id = article.get('articleId') or article.get('article_id') or 0
        article_no = article.get('articleNo') or article.get('article_no') or self.article_number
        article_name = (
            article.get('articleName')
            or article.get('article_name')
            or article.get('articleProductName')
            or article.get('article_product_name')
            or article.get('genericArticleName')
            or article.get('generic_article_name')
            or ''
        )
        supplier_name = (
            article.get('supplierName')
            or article.get('supplier_name')
            or article.get('brandName')
            or article.get('brand_name')
            or ''
        )
        parts = []
        if supplier_name:
            parts.append(supplier_name)
        if supplier_id:
            parts.append(f"supplierId={supplier_id}")
        if article_id:
            parts.append(f"articleId={article_id}")
        parts.append(f"articleNo={article_no}")
        if article_name:
            parts.append(article_name)
        return {
            'supplier_id': supplier_id,
            'supplier_name': supplier_name,
            'article_id': article_id,
            'article_no': article_no,
            'article_name': article_name,
            'display_summary': " / ".join(parts),
        }

    def _fetch_candidate_articles(self):
        self.ensure_one()
        api = self.env['tecdoc.api']._get_default_api()
        if not api:
            raise UserError("Please configure TecDoc API first!")

        supplier_id = self.supplier_id or None
        if self.lookup_type == 'oem':
            results = api.search_articles_by_oem_no(self.article_number)
        elif self.lookup_type == 'ean':
            results = api.search_articles_by_ean(self.article_number)
        else:
            results = (
                api.search_article_by_number_and_supplier(self.article_number, supplier_id)
                if supplier_id
                else api.search_article_by_number(self.article_number)
            )
        candidates = api._extract_articles(results)
        try:
            if self.lookup_type == 'article_no':
                alt = api.search_articles_by_article_no(self.article_number, article_type='ArticleNumber')
                candidates.extend([a for a in api._extract_articles(alt) if a not in candidates])
        except UserError:
            pass

        unique = []
        seen = set()
        for article in candidates:
            if not isinstance(article, dict):
                continue
            key = self._candidate_key(article)
            if key in seen:
                continue
            seen.add(key)
            unique.append(article)
        return unique

    def _product_variant_for_apply(self, product):
        product = product if product._name == 'product.product' else product.product_variant_id
        return product.exists() if product else product

    def _apply_to_invoice_ingest_line(self, product, confirmation_source='tecdoc_sync'):
        self.ensure_one()
        line = self.invoice_ingest_line_id.exists()
        if not line:
            return False

        product_variant = self._product_variant_for_apply(product)
        if not product_variant:
            return False

        canonical_brand, canonical_supplier_id = line.job_id._brand_from_matched_product(product_variant)
        line.with_context(skip_audit_log=True).write({
            'product_id': product_variant.id,
            'supplier_brand': canonical_brand or line.supplier_brand,
            'supplier_brand_id': canonical_supplier_id or line.supplier_brand_id or False,
            'match_method': 'exact:tecdoc_sync',
            'match_confidence': 100.0,
        })
        self.env['invoice.product.code.map'].create_or_update_from_line(
            line,
            product_variant,
            normalized_code=self.article_number,
            confirmation_source=confirmation_source,
            tecdoc_supplier_id=self.supplier_id or product_variant.tecdoc_supplier_id or False,
        )
        line.job_id._audit_log(
            action='custom',
            description=f'Invoice ingest line matched through TecDoc: {line.job_id.display_name} / line {line.sequence}',
            new_values={
                'line_id': line.id,
                'sequence': line.sequence,
                'product_id': product_variant.id,
                'tecdoc_lookup_type': self.lookup_type,
                'tecdoc_search_value': self.article_number,
                'tecdoc_supplier_id': self.supplier_id or False,
                'match_method': 'exact:tecdoc_sync',
                'match_confidence': 100.0,
            },
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'TecDoc Match',
                'message': 'Product created from TecDoc and linked to the invoice line.',
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def action_preview_candidates(self):
        """Preview potential matches so the user can pick a supplier_id when needed."""
        self.ensure_one()
        candidates = self._fetch_candidate_articles()
        self.candidate_ids.unlink()
        self.write({
            'candidate_ids': [
                (0, 0, self._candidate_values(article))
                for article in candidates[:20]
            ],
        })

        if not candidates:
            self.candidates_info = f"No matches found for: {self.article_number}"
        else:
            lines = [
                "Pick a row with Use, or copy supplierId if you prefer manual entry.",
                "",
                "Matches:",
            ]
            for candidate in self.candidate_ids:
                lines.append(" - " + candidate.display_summary)
            if len(candidates) > 20:
                lines.append("")
                lines.append(f"(showing first 20 of {len(candidates)} matches)")
            self.candidates_info = "\n".join(lines)

        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _sync_product_from_lookup(self):
        """Return a synced product for the wizard lookup settings."""
        self.ensure_one()
        api = self.env['tecdoc.api']._get_default_api()

        if not api:
            raise UserError("Please configure TecDoc API first!")

        supplier_id = self.supplier_id or None

        # Prefer syncing directly by article number when that's what the user searched by.
        try:
            if self.lookup_type == 'article_no':
                product = api.sync_product_from_tecdoc(article_no=self.article_number, supplier_id=supplier_id)
            else:
                raise UserError("Lookup requires pre-search.")
        except UserError as e:
            # Fall back to search + best-effort details.
            if self.lookup_type == 'oem':
                results = api.search_articles_by_oem_no(self.article_number)
            elif self.lookup_type == 'ean':
                results = api.search_articles_by_ean(self.article_number)
            else:
                results = (
                    api.search_article_by_number_and_supplier(self.article_number, supplier_id)
                    if supplier_id
                    else api.search_article_by_number(self.article_number)
                )
            candidates = api._extract_articles(results)
            try:
                if self.lookup_type == 'article_no':
                    alt = api.search_articles_by_article_no(self.article_number, article_type='ArticleNumber')
                    candidates.extend([a for a in api._extract_articles(alt) if a not in candidates])
            except UserError:
                pass
            if not candidates:
                raise UserError(f"No article found for: {self.article_number}")
            if not supplier_id and len(candidates) > 1:
                raise UserError(
                    "Multiple TecDoc matches found. Click “Find Supplier IDs”, then use one candidate row."
                )

            _logger.info(
                "TecDoc: %s candidate(s) for article_no=%s; first keys=%s",
                len(candidates),
                self.article_number,
                sorted(list(candidates[0].keys())) if isinstance(candidates[0], dict) else [],
            )

            last_error = e
            for article in candidates[:10]:
                article_id = article.get('articleId') or article.get('article_id')
                candidate_no = article.get('articleNo') or self.article_number
                try:
                    if supplier_id and (article.get('supplierId') or article.get('supplier_id')) not in {supplier_id}:
                        continue
                    product = api.sync_product_from_tecdoc(
                        article_id=article_id,
                        article_no=candidate_no,
                        supplier_id=supplier_id,
                    )
                    break
                except UserError as err:
                    last_error = err
                    if api._is_not_found_error(err):
                        continue
                    raise
            else:
                if api._is_not_found_error(last_error):
                    # Fallback: create product from search result snippet (still useful if detail endpoints are unavailable).
                    product = api.sync_product_from_article_snippet(candidates[0], fallback_article_no=self.article_number)
                else:
                    raise last_error

        return product

    @api.model
    def sync_product_for_lookup(self, lookup_type='article_no', article_number=None, supplier_id=False):
        """Sync a TecDoc product and return a many2one-friendly product value."""
        article_number = (article_number or '').strip()
        if not article_number:
            raise UserError("Enter an article number first.")

        wizard = self.create({
            'lookup_type': lookup_type or 'article_no',
            'article_number': article_number,
            'supplier_id': supplier_id or False,
        })
        product = wizard._sync_product_from_lookup()
        variant = product if product._name == 'product.product' else product.product_variant_id
        return {
            'id': variant.id,
            'display_name': variant.display_name,
        }

    def action_sync(self):
        """Sync product from TecDoc"""
        product = self._sync_product_from_lookup()
        template = product.product_tmpl_id if product._name == 'product.product' else product
        line_action = self._apply_to_invoice_ingest_line(product, confirmation_source='tecdoc_sync')
        if line_action:
            return line_action
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'product.template',
            'res_id': template.id,
            'view_mode': 'form',
            'target': 'current',
        }


class TecDocSyncWizardCandidate(models.TransientModel):
    _name = 'tecdoc.sync.wizard.candidate'
    _description = 'TecDoc Sync Wizard Candidate'
    _order = 'supplier_name, supplier_id, article_no'

    wizard_id = fields.Many2one('tecdoc.sync.wizard', required=True, ondelete='cascade')
    supplier_id = fields.Integer('Supplier ID', index=True)
    supplier_name = fields.Char('Supplier')
    article_id = fields.Integer('Article ID', index=True)
    article_no = fields.Char('Article No')
    article_name = fields.Char('Name')
    display_summary = fields.Char('Summary')

    def action_use_candidate(self):
        self.ensure_one()
        wizard = self.wizard_id
        api = self.env['tecdoc.api']._get_default_api()
        if not api:
            raise UserError("Please configure TecDoc API first!")

        wizard.write({
            'supplier_id': self.supplier_id or 0,
            'article_number': self.article_no or wizard.article_number,
        })
        product = api.sync_product_from_tecdoc(
            article_id=self.article_id or None,
            article_no=self.article_no or wizard.article_number,
            supplier_id=self.supplier_id or None,
        )
        line_action = wizard._apply_to_invoice_ingest_line(product, confirmation_source='tecdoc_candidate')
        if line_action:
            return line_action
        template = product.product_tmpl_id if product._name == 'product.product' else product
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'product.template',
            'res_id': template.id,
            'view_mode': 'form',
            'target': 'current',
        }
