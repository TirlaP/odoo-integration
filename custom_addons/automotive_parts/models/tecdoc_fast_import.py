# -*- coding: utf-8 -*-
import glob
import json
import os
import re
import logging
import time
import random

from odoo import api, fields, models
from odoo.exceptions import UserError

import psycopg2
from psycopg2 import errorcodes

from .tecdoc_fast_models import _normalize_key

_logger = logging.getLogger(__name__)
_RUN_LOCK_NAMESPACE = 41731


def _safe_int(value):
    try:
        return int(value)
    except Exception:
        return False


def _parse_date(value):
    value = (value or '').strip()
    if not value:
        return False
    # TecDoc returns YYYY-MM-DD
    return value[:10]


def _extract_eans(ean_numbers):
    """
    TecDoc sometimes returns a single string or null.
    Extract digit tokens (8..14) from the payload.
    """
    if not ean_numbers:
        return []
    if isinstance(ean_numbers, list):
        raw = ' '.join(str(x) for x in ean_numbers if x)
    else:
        raw = str(ean_numbers)
    tokens = re.findall(r'\d{8,14}', raw)
    # Deduplicate while preserving order
    seen = set()
    out = []
    for t in tokens:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _normalize_supplier_key(value):
    # Supplier names can include spaces / punctuation; normalize like the JS fetcher.
    return re.sub(r'[^0-9A-Z]+', '', (value or '').strip().upper())


class TecDocFastImportRun(models.Model):
    _name = 'tecdoc.fast.import.run'
    _description = 'TecDoc Fast Import Run'
    _order = 'id desc'

    name = fields.Char(required=True, default=lambda self: f"TecDoc Import {fields.Datetime.now()}")
    directory = fields.Char(
        required=True,
        help='Path to exported TecDoc JSON folder. You can point to the export root (contains by_code/ and/or by_article/) '
             'or directly to by_code/ or by_article/.',
    )
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('running', 'Running'),
            ('done', 'Done'),
            ('failed', 'Failed'),
        ],
        default='draft',
        index=True,
    )

    cursor = fields.Integer(default=0, help='Next file index to process (sorted).')
    batch_size = fields.Integer(default=25)

    run_mode = fields.Selection(
        [
            ('full', 'Full Import'),
            ('xrefs_only', 'Cross References Only'),
        ],
        default='full',
        required=True,
        help='Full import creates/updates products, variants, OEM/specs/vehicles. '
             'Cross References Only updates only cross-ref tables for existing variants.',
    )

    replace_variant_details = fields.Boolean(
        default=True,
        help='Replace per-variant lists from JSON (clears stale EAN/spec/cross rows and clears OEM/vehicle links when missing).',
    )

    mark_products_managed = fields.Boolean(
        default=True,
        help='Mark products touched by this importer as TecDoc Fast managed (used for purge/archive).',
    )

    import_cross_references = fields.Boolean(
        default=True,
        help='If JSON files contain cross references, import them too.',
    )

    processed = fields.Integer(default=0)
    created_products = fields.Integer(default=0)
    created_variants = fields.Integer(default=0)
    updated_variants = fields.Integer(default=0)
    created_vehicles = fields.Integer(default=0)
    created_oem_numbers = fields.Integer(default=0)
    created_cross_numbers = fields.Integer(default=0)

    last_error = fields.Text()
    started_at = fields.Datetime()
    finished_at = fields.Datetime()

    def _audit_log(self, description, payload=None):
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                self.env['automotive.audit.log'].log_change(
                    action='custom',
                    record=self,
                    description=description,
                    new_values=payload or {},
                )
        except Exception as exc:
            _logger.warning("TecDoc Fast: failed to write audit log for run %s: %s", self.id, exc)

    @staticmethod
    def _is_retryable_tx_error(exc):
        msg = str(exc or '').lower()
        if 'could not serialize access due to concurrent update' in msg:
            return True
        if 'deadlock detected' in msg:
            return True
        if isinstance(exc, psycopg2.errors.SerializationFailure):
            return True
        if isinstance(exc, psycopg2.errors.DeadlockDetected):
            return True
        if isinstance(exc, psycopg2.Error):
            return getattr(exc, 'pgcode', None) in (
                errorcodes.SERIALIZATION_FAILURE,
                errorcodes.DEADLOCK_DETECTED,
            )
        return False

    def _acquire_run_lock(self):
        self.ensure_one()
        self.env.cr.execute("SELECT pg_try_advisory_lock(%s, %s)", (_RUN_LOCK_NAMESPACE, self.id))
        row = self.env.cr.fetchone()
        return bool(row and row[0])

    def _release_run_lock(self):
        self.ensure_one()
        try:
            self.env.cr.execute("SELECT pg_advisory_unlock(%s, %s)", (_RUN_LOCK_NAMESPACE, self.id))
        except Exception:
            # Connection might already be reset/closed; ignore unlock errors.
            pass

    def action_start(self):
        for rec in self:
            if rec.state not in ('draft', 'failed'):
                continue
            rec.write({
                'state': 'running',
                'started_at': fields.Datetime.now(),
                'finished_at': False,
                'last_error': False,
            })
            rec._audit_log('TecDoc Fast import started', {
                'directory': rec.directory,
                'run_mode': rec.run_mode,
                'batch_size': rec.batch_size,
                'replace_variant_details': rec.replace_variant_details,
                'mark_products_managed': rec.mark_products_managed,
                'import_cross_references': rec.import_cross_references,
            })

    def action_reset(self):
        for rec in self:
            rec.write({
                'state': 'draft',
                'cursor': 0,
                'processed': 0,
                'created_products': 0,
                'created_variants': 0,
                'updated_variants': 0,
                'created_vehicles': 0,
                'created_oem_numbers': 0,
                'created_cross_numbers': 0,
                'last_error': False,
                'started_at': False,
                'finished_at': False,
            })
            rec._audit_log('TecDoc Fast import reset', {
                'directory': rec.directory,
                'run_mode': rec.run_mode,
            })

    def action_open_purge_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Purge TecDoc Fast Data',
            'res_model': 'tecdoc.fast.purge.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_run_id': self.id},
        }

    def _list_files(self):
        self.ensure_one()
        base = (self.directory or '').strip()
        if not base:
            raise UserError('Import directory is required.')

        def _list_json_files(dir_path):
            if not dir_path or not os.path.isdir(dir_path):
                return []
            return sorted(glob.glob(os.path.join(dir_path, '*.json')))

        # Allow users to point directly at a folder full of JSON files (e.g. .../by_article).
        direct = _list_json_files(base)
        if direct:
            return direct

        by_article = os.path.join(base, 'by_article')
        by_code = os.path.join(base, 'by_code')

        # Prefer by_article when present (split-per-supplier/per-article exports).
        article_files = _list_json_files(by_article)
        if article_files:
            return article_files

        code_files = _list_json_files(by_code)
        if code_files:
            return code_files

        hint = ''
        if os.path.isdir(base) and os.path.basename(os.path.normpath(base)) in ('by_article', 'by_code'):
            hint = ' (tip: set Directory to the export root folder, not inside by_article/by_code)'
        raise UserError(
            f'Invalid directory (no JSON files found). Expected either JSON files directly in: {base} '
            f'or a child folder: {by_article} or {by_code}.{hint}'
        )

    @api.model
    def _cron_process_import_runs(self):
        runs = self.search([('state', '=', 'running')], order='id asc', limit=1)
        for run in runs:
            run._process_batch()

    def _process_batch(self):
        self.ensure_one()
        if self.state != 'running':
            return
        if not self._acquire_run_lock():
            _logger.info("TecDoc Fast: run %s is already being processed by another worker, skipping.", self.id)
            return

        try:
            files = self._list_files()
            if self.cursor >= len(files):
                self.write({'state': 'done', 'finished_at': fields.Datetime.now()})
                self._audit_log('TecDoc Fast import finished', {
                    'directory': self.directory,
                    'run_mode': self.run_mode,
                    'processed': self.processed,
                    'created_products': self.created_products,
                    'created_variants': self.created_variants,
                    'updated_variants': self.updated_variants,
                    'created_vehicles': self.created_vehicles,
                    'created_oem_numbers': self.created_oem_numbers,
                    'created_cross_numbers': self.created_cross_numbers,
                })
                return

            start = self.cursor
            end = min(len(files), start + max(1, self.batch_size))
            to_process = files[start:end]

            for file_path in to_process:
                max_attempts = 8
                for attempt in range(1, max_attempts + 1):
                    try:
                        # Keep one bad/contended file from aborting the whole batch transaction.
                        with self.env.cr.savepoint():
                            self._process_file(file_path)
                        break
                    except Exception as exc:
                        if self._is_retryable_tx_error(exc) and attempt < max_attempts:
                            wait_s = (0.05 * attempt) + random.uniform(0.0, 0.05)
                            _logger.warning(
                                "TecDoc Fast: retrying file after concurrent update (attempt %s/%s) file=%s err=%s",
                                attempt + 1,
                                max_attempts,
                                file_path,
                                exc,
                            )
                            time.sleep(wait_s)
                            continue
                        raise

                self.cursor += 1
                self.processed += 1

            self.env.cr.commit()
        except Exception as exc:
            # Transient DB contention can still happen at flush/commit time.
            # Do not fail the run for retryable tx errors; retry on next loop.
            if self._is_retryable_tx_error(exc):
                self.env.cr.rollback()
                _logger.warning(
                    "TecDoc Fast: transient tx conflict for run %s at cursor=%s processed=%s; will retry batch: %s",
                    self.id,
                    self.cursor,
                    self.processed,
                    exc,
                )
                # Keep the run in 'running' state; caller loop will invoke _process_batch() again.
                return

            # Critical: clear aborted transaction state before writing failure/audit rows.
            self.env.cr.rollback()
            self.write({'state': 'failed', 'last_error': str(exc)})
            self._audit_log('TecDoc Fast import failed', {
                'directory': self.directory,
                'run_mode': self.run_mode,
                'cursor': self.cursor,
                'processed': self.processed,
                'error': str(exc),
            })
            self.env.cr.commit()
        finally:
            self._release_run_lock()

    def _process_file(self, file_path):
        self.ensure_one()
        with open(file_path, 'r', encoding='utf-8') as f:
            payload = json.load(f)

        outcome = payload.get('outcome')
        if outcome and outcome != 'found':
            return

        tecdoc = payload.get('tecdoc') or {}
        # Support both exporter payload shapes:
        # 1) JS/XML exporter: tecdoc.articleNumberDetails + inputLines[]
        # 2) CSV fetcher:     tecdoc.search_result + input.part
        details = (
            tecdoc.get('articleNumberDetails')
            or tecdoc.get('article_number_details')
            or tecdoc.get('search_result')
            or tecdoc.get('searchResult')
            or {}
        )
        if not isinstance(details, dict):
            return

        input_lines = payload.get('inputLines') or payload.get('input_lines') or []
        if not input_lines:
            input_obj = payload.get('input') or {}
            part = input_obj.get('part') if isinstance(input_obj, dict) else {}
            if isinstance(part, dict) and part:
                input_lines = [{
                    'Denumire': (part.get('name') or '').strip(),
                    'Cod_bare': (part.get('barcode') or '').strip(),
                    'Pret': (part.get('price') or '').strip(),
                    'Cod': (part.get('order_code') or '').strip(),
                }]

        article_no = (
            (details.get('articleNo') or '').strip()
            or (payload.get('code') or '').strip()
            or ((input_lines[0].get('Cod') or '').strip() if input_lines else '')
        )
        if not article_no:
            return

        articles = details.get('articles') or []
        if not isinstance(articles, list):
            return

        if self.run_mode == 'xrefs_only':
            self._process_xrefs_only(article_no, articles, tecdoc)
            return

        first_line = input_lines[0] if input_lines else {}

        product = self._upsert_product(article_no, first_line)

        # Product image: pick one representative image URL from the variants
        image_url = None
        for a in articles:
            if not isinstance(a, dict):
                continue
            url = (a.get('s3image') or '').strip()
            if url:
                image_url = url
                break
        if image_url and not product.tecdoc_image_url:
            try:
                product.write({'tecdoc_image_url': image_url})
            except Exception:
                pass

        for article in articles:
            if not isinstance(article, dict):
                continue
            self._upsert_variant(product, article, tecdoc if self.import_cross_references else None)

        if not product.tecdoc_article_no_key:
            product.tecdoc_article_no_key = _normalize_key(product.tecdoc_article_no)

        if self.mark_products_managed:
            product.tecdoc_fast_managed = True
            product.tecdoc_fast_last_import_at = fields.Datetime.now()

        # Optionally download image_1920 using the same flags as the TecDoc live sync:
        # tecdoc.api.download_images + (overwrite_images or missing image)
        self._maybe_sync_product_image(product, image_url)

    def _process_xrefs_only(self, article_no, articles, tecdoc_payload):
        if not self.import_cross_references:
            return
        Variant = self.env['tecdoc.article.variant'].sudo()
        for article in articles:
            if not isinstance(article, dict):
                continue
            article_id = _safe_int(article.get('articleId'))
            if not article_id:
                continue
            variant = Variant.search([('article_id', '=', article_id)], limit=1)
            if not variant:
                continue
            if self.replace_variant_details:
                self.env['tecdoc.article.variant.cross'].sudo().search([('variant_id', '=', variant.id)]).unlink()
            self._upsert_variant_cross_refs(variant, tecdoc_payload)
            variant.cross_count = self.env['tecdoc.article.variant.cross'].sudo().search_count([('variant_id', '=', variant.id)])

    def _maybe_sync_product_image(self, product, image_url):
        if not product or not image_url:
            return
        api = self.env['tecdoc.api']._get_default_api()
        if not api:
            return
        should_download = api.download_images and (api.overwrite_images or not product.image_1920)
        if not should_download:
            return
        try:
            image_b64 = api._fetch_image_base64(image_url)
            if image_b64:
                product.with_context(skip_audit_log=True).write({'image_1920': image_b64})
        except Exception as exc:
            _logger.info("TecDoc Fast: image download failed for product %s url=%s err=%s", product.id, image_url, exc)

    def _upsert_product(self, article_no, first_line):
        Product = self.env['product.template'].sudo().with_context(skip_audit_log=True)
        key = _normalize_key(article_no)
        product = Product.search([('tecdoc_article_no_key', '=', key)], limit=1)
        if not product:
            vals = {
                'name': (first_line.get('Denumire') or article_no)[:255],
                'tecdoc_article_no': article_no,
                'tecdoc_article_no_key': key,
                # TecDoc parts should be stock-tracked by default.
                'type': 'consu',
                'is_storable': True,
            }
            # Integrate with Odoo core: use Internal Reference as the article number when possible.
            vals['default_code'] = article_no
            if first_line.get('Cod_bare'):
                vals['barcode'] = first_line.get('Cod_bare')
            if first_line.get('Pret'):
                try:
                    vals['list_price'] = float(str(first_line.get('Pret')).replace(',', '.'))
                except Exception:
                    pass
            try:
                with self.env.cr.savepoint():
                    product = Product.create(vals)
            except Exception as exc:
                # Keep bulk imports resilient: barcode collisions are common across vendor files.
                # Retry once without barcode so the product still gets imported and can be matched by article code.
                msg = str(exc).lower()
                if vals.get('barcode') and ('barcode' in msg or 'cod bare' in msg):
                    _logger.warning(
                        "TecDoc Fast: duplicate barcode %s for article %s; creating product without barcode",
                        vals.get('barcode'),
                        article_no,
                    )
                    vals_no_barcode = dict(vals)
                    vals_no_barcode.pop('barcode', None)
                    with self.env.cr.savepoint():
                        product = Product.create(vals_no_barcode)
                else:
                    raise
            self.created_products += 1
        else:
            # Keep current name/price unless missing
            vals = {}
            if not product.tecdoc_article_no:
                vals['tecdoc_article_no'] = article_no
            if not product.tecdoc_article_no_key:
                vals['tecdoc_article_no_key'] = key
            if not product.default_code:
                vals['default_code'] = article_no
            if (not product.name or product.name.strip() == article_no) and first_line.get('Denumire'):
                vals['name'] = first_line.get('Denumire')[:255]
            if vals:
                product.write(vals)
        if self.mark_products_managed:
            try:
                product.write({'tecdoc_fast_managed': True, 'tecdoc_fast_last_import_at': fields.Datetime.now()})
            except Exception:
                pass
        return product

    def _upsert_variant(self, product, article, tecdoc_payload):
        Variant = self.env['tecdoc.article.variant'].sudo()
        Supplier = self.env['tecdoc.supplier'].sudo()
        product = product[:1] if product else self.env['product.template']

        article_id = _safe_int(article.get('articleId') or article.get('article_id'))
        if not article_id:
            variant = Variant._upsert_light_reference(article)
            article_id = variant.article_id if variant else 0
        if not article_id:
            return

        article_no = (article.get('articleNo') or article.get('article_no') or (product.tecdoc_article_no if product else '') or '').strip()
        if not article_no:
            return

        supplier_id_int = _safe_int(article.get('supplierId')) or 0
        supplier_name = (article.get('supplierName') or '').strip()
        supplier = False
        if supplier_id_int:
            supplier = Supplier.search([('supplier_id', '=', supplier_id_int)], limit=1)
            if not supplier:
                supplier = Supplier.create({'supplier_id': supplier_id_int, 'name': supplier_name or str(supplier_id_int)})
        elif supplier_name:
            supplier = Supplier.search([('name', '=', supplier_name)], limit=1)
            if not supplier:
                supplier = Supplier.create({'supplier_id': 0, 'name': supplier_name})

        vals = {
            'article_id': article_id,
            'article_no': article_no,
            'article_no_key': _normalize_key(article_no),
            'supplier_id': supplier.id if supplier else False,
            'supplier_name': supplier_name,
            'supplier_external_id': supplier_id_int or False,
            'article_product_name': (article.get('articleProductName') or '').strip(),
            'image_url': article.get('s3image') or False,
            'media_filename': article.get('articleMediaFileName') or False,
            'media_type': article.get('articleMediaType') or False,
        }
        if product:
            vals['product_tmpl_id'] = product.id
            vals['is_reference_only'] = False

        variant = Variant.search([('article_id', '=', article_id)], limit=1)
        if not variant:
            identity_key = vals.get('identity_key') or Variant._article_values_from_payload(article).get('identity_key')
            variant = Variant.search([('identity_key', '=', identity_key)], limit=1) if identity_key else Variant.browse()
        if not variant and vals.get('article_no_key') and vals.get('supplier_external_id'):
            variant = Variant.search([
                ('article_no_key', '=', vals['article_no_key']),
                ('supplier_external_id', '=', vals['supplier_external_id']),
            ], limit=1)
        created = False
        if not variant:
            variant = Variant.create(vals)
            created = True
            self.created_variants += 1
        else:
            variant.write(vals)
            self.updated_variants += 1

        replace = bool(self.replace_variant_details)

        # EANs
        eans = []
        ean_no = article.get('eanNo') or {}
        if isinstance(ean_no, dict):
            eans = _extract_eans(ean_no.get('eanNumbers'))
        self._upsert_variant_eans(variant, eans, replace=replace)

        # OEM numbers
        self._upsert_variant_oem_numbers(variant, article.get('oemNo') or [], replace=replace)

        # Specs
        self._upsert_variant_specs(variant, article.get('allSpecifications') or [], replace=replace)

        # Vehicles
        self._upsert_variant_vehicles(variant, article.get('compatibleCars') or [], replace=replace)

        # Cross references (if present in JSON)
        if self.import_cross_references and tecdoc_payload:
            self._upsert_variant_cross_refs(variant, tecdoc_payload, replace=replace)

        # Update counters
        variant.vehicle_count = len(variant.vehicle_ids)
        variant.oem_count = len(variant.oem_number_ids)
        variant.criteria_count = self.env['tecdoc.article.variant.criteria'].sudo().search_count([('variant_id', '=', variant.id)])
        variant.ean_count = self.env['tecdoc.article.variant.ean'].sudo().search_count([('variant_id', '=', variant.id)])
        variant.cross_count = self.env['tecdoc.article.variant.cross'].sudo().search_count([('variant_id', '=', variant.id)])

        if created and product and not product.tecdoc_supplier_name and supplier_name:
            product.write({'tecdoc_supplier_name': supplier_name, 'tecdoc_supplier_id': supplier_id_int})

    def _upsert_variant_eans(self, variant, eans, replace=False):
        Ean = self.env['tecdoc.article.variant.ean'].sudo()
        if replace:
            Ean.search([('variant_id', '=', variant.id)]).unlink()
        if not eans:
            return
        existing = set(Ean.search([('variant_id', '=', variant.id)]).mapped('ean_key'))
        to_create = []
        for ean in eans:
            key = _normalize_key(ean)
            if not key or key in existing:
                continue
            to_create.append({'variant_id': variant.id, 'ean': ean, 'ean_key': key})
            existing.add(key)
        if to_create:
            Ean.create(to_create)

    def _upsert_variant_oem_numbers(self, variant, oem_list, replace=False):
        if not isinstance(oem_list, list) or not oem_list:
            if replace:
                variant.write({'oem_number_ids': [(6, 0, [])]})
            return
        Oem = self.env['tecdoc.oem.number'].sudo()

        pairs = []
        for item in oem_list:
            if not isinstance(item, dict):
                continue
            brand = (item.get('oemBrand') or '').strip()
            display = (item.get('oemDisplayNo') or '').strip()
            if not brand or not display:
                continue
            key = _normalize_key(display)
            pairs.append((brand, key, display))

        if not pairs:
            if replace:
                variant.write({'oem_number_ids': [(6, 0, [])]})
            return

        existing = Oem.search([('brand', 'in', [p[0] for p in pairs]), ('number_key', 'in', [p[1] for p in pairs])])
        existing_map = {(r.brand, r.number_key): r.id for r in existing}

        to_create = []
        for brand, key, display in pairs:
            if (brand, key) in existing_map:
                continue
            to_create.append({'brand': brand, 'display_no': display, 'number_key': key})

        created = Oem.create(to_create) if to_create else self.env['tecdoc.oem.number']
        if created:
            self.created_oem_numbers += len(created)
            for r in created:
                existing_map[(r.brand, r.number_key)] = r.id

        # Link
        oem_ids = list({existing_map[(b, k)] for b, k, _d in pairs if (b, k) in existing_map})
        if oem_ids:
            variant.write({'oem_number_ids': [(6, 0, oem_ids)]})

    def _upsert_variant_specs(self, variant, spec_list, replace=False):
        if not isinstance(spec_list, list) or not spec_list:
            if replace:
                self.env['tecdoc.article.variant.criteria'].sudo().search([('variant_id', '=', variant.id)]).unlink()
            return
        Criteria = self.env['tecdoc.criteria'].sudo()
        Value = self.env['tecdoc.article.variant.criteria'].sudo()

        names = []
        rows = []
        for item in spec_list:
            if not isinstance(item, dict):
                continue
            name = (item.get('criteriaName') or '').strip()
            value = (item.get('criteriaValue') or '').strip()
            if not name:
                continue
            names.append(name)
            rows.append((name, value))

        if not rows:
            return

        name_keys = {_normalize_key(n): n for n in names}
        existing = Criteria.search([('name_key', 'in', list(name_keys.keys()))])
        criteria_map = {c.name_key: c.id for c in existing}
        missing = [{'name': name_keys[k], 'name_key': k} for k in name_keys.keys() if k not in criteria_map]
        created = Criteria.create(missing) if missing else self.env['tecdoc.criteria']
        for c in created:
            criteria_map[c.name_key] = c.id

        if replace:
            Value.search([('variant_id', '=', variant.id)]).unlink()
            existing_set = set()
        else:
            existing_values = Value.search([('variant_id', '=', variant.id)])
            existing_set = {(v.criteria_id.id, v.value_text or '') for v in existing_values}
        to_create = []
        for name, value in rows:
            cid = criteria_map.get(_normalize_key(name))
            if not cid:
                continue
            key = (cid, value or '')
            if key in existing_set:
                continue
            existing_set.add(key)
            to_create.append({'variant_id': variant.id, 'criteria_id': cid, 'value_text': value or False})
        if to_create:
            Value.create(to_create)

    def _upsert_variant_vehicles(self, variant, cars_list, replace=False):
        if not isinstance(cars_list, list) or not cars_list:
            if replace:
                variant.write({'vehicle_ids': [(6, 0, [])]})
            return
        Vehicle = self.env['tecdoc.vehicle'].sudo()

        vehicle_ids = []
        vals_by_vehicle_id = {}
        for item in cars_list:
            if not isinstance(item, dict):
                continue
            vid = _safe_int(item.get('vehicleId'))
            if not vid:
                continue
            vehicle_ids.append(vid)
            vals_by_vehicle_id[vid] = {
                'vehicle_id': vid,
                'model_id': _safe_int(item.get('modelId')) or False,
                'manufacturer_name': (item.get('manufacturerName') or '').strip(),
                'model_name': (item.get('modelName') or '').strip(),
                'type_engine_name': (item.get('typeEngineName') or '').strip(),
                'construction_interval_start': _parse_date(item.get('constructionIntervalStart')) or False,
                'construction_interval_end': _parse_date(item.get('constructionIntervalEnd')) or False,
            }

        if not vehicle_ids:
            return

        existing = Vehicle.search([('vehicle_id', 'in', list(set(vehicle_ids)))])
        existing_map = {v.vehicle_id: v.id for v in existing}
        to_create = [vals_by_vehicle_id[vid] for vid in vals_by_vehicle_id.keys() if vid not in existing_map]
        created = Vehicle.create(to_create) if to_create else self.env['tecdoc.vehicle']
        if created:
            self.created_vehicles += len(created)
            for v in created:
                existing_map[v.vehicle_id] = v.id

        rel_ids = [existing_map[vid] for vid in vals_by_vehicle_id.keys() if vid in existing_map]
        if rel_ids:
            variant.write({'vehicle_ids': [(6, 0, rel_ids)]})

    def _upsert_variant_cross_refs(self, variant, tecdoc_payload, replace=False):
        cross_by_supplier = tecdoc_payload.get('crossReferencesBySupplier') or []
        if not isinstance(cross_by_supplier, list) or not cross_by_supplier:
            if replace:
                self.env['tecdoc.article.variant.cross'].sudo().search([('variant_id', '=', variant.id)]).unlink()
            return

        Supplier = self.env['tecdoc.supplier'].sudo()
        CrossNumber = self.env['tecdoc.cross.number'].sudo()
        CrossLink = self.env['tecdoc.article.variant.cross'].sudo()

        # Only import cross references for THIS variant's supplier.
        variant_supplier_key = _normalize_supplier_key(variant.supplier_name or variant.supplier_id.name)
        if not variant_supplier_key:
            return
        relevant_entries = []
        for entry in cross_by_supplier:
            if not isinstance(entry, dict):
                continue
            if _normalize_supplier_key(entry.get('supplierName')) == variant_supplier_key:
                relevant_entries.append(entry)
        if not relevant_entries:
            return

        links_to_create = []
        if replace:
            existing_set = set()
        else:
            existing_links = CrossLink.search([('variant_id', '=', variant.id)])
            existing_set = {(l.cross_number_id.id, l.search_level or '') for l in existing_links}

        for entry in relevant_entries:
            if not isinstance(entry, dict):
                continue
            supplier_name = (entry.get('supplierName') or '').strip()
            supplier = Supplier.search([('name', '=', supplier_name)], limit=1) if supplier_name else False
            resp = entry.get('response') or {}
            if not isinstance(resp, dict):
                continue
            articles = resp.get('articles') or []
            if not isinstance(articles, list):
                continue
            for item in articles:
                if not isinstance(item, dict):
                    continue
                manufacturer = (item.get('crossManufacturerName') or '').strip()
                display = (item.get('crossNumber') or '').strip()
                search_level = (item.get('searchLevel') or '').strip()
                if not manufacturer or not display:
                    continue
                key = _normalize_key(display)
                cross = CrossNumber.search([('manufacturer', '=', manufacturer), ('number_key', '=', key)], limit=1)
                if not cross:
                    cross = CrossNumber.create({'manufacturer': manufacturer, 'display_no': display, 'number_key': key})
                    self.created_cross_numbers += 1

                dedupe_key = (cross.id, search_level)
                if dedupe_key in existing_set:
                    continue
                existing_set.add(dedupe_key)
                links_to_create.append({
                    'variant_id': variant.id,
                    'cross_number_id': cross.id,
                    'search_level': search_level or False,
                    'source_supplier_id': supplier.id if supplier else False,
                    'source_supplier_name': supplier_name or False,
                    'article_brand_root': (item.get('articleBrandRoot') or '').strip() or False,
                    'article_number_root': (item.get('articleNumberRoot') or '').strip() or False,
                })

        if links_to_create:
            CrossLink.create(links_to_create)
