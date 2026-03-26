# -*- coding: utf-8 -*-
import hashlib
import json

from odoo import models, fields, api


class TecDocApiCache(models.Model):
    _name = 'tecdoc.api.cache'
    _description = 'TecDoc API Cache'
    _order = 'last_hit_at desc, create_date desc'

    api_id = fields.Many2one('tecdoc.api', required=True, ondelete='cascade', index=True)
    cache_key = fields.Char(required=True, index=True)

    method = fields.Char(required=True, default='GET', index=True)
    endpoint = fields.Char(required=True, index=True)
    params_json = fields.Text()
    body_json = fields.Text()

    ok = fields.Boolean(default=True, index=True)
    status_code = fields.Integer()
    error = fields.Text()

    response_json = fields.Text()
    fetched_at = fields.Datetime(index=True)
    expires_at = fields.Datetime(index=True)

    hits = fields.Integer(default=0)
    last_hit_at = fields.Datetime(index=True)

    _sql_constraints = [
        ('tecdoc_api_cache_key_unique', 'unique(api_id, cache_key)', 'Cache key must be unique per TecDoc API config.'),
    ]

    @api.model
    def make_cache_key(self, base_url, method, endpoint, params, body):
        normalized_params = params or {}
        payload = {
            'base_url': base_url or '',
            'method': (method or 'GET').upper(),
            'endpoint': endpoint or '',
            'params': normalized_params,
            'body': body or None,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        return hashlib.sha256(encoded).hexdigest()

    @api.model
    def _legacy_cache_key(self, base_url, endpoint, params):
        payload = {
            'base_url': base_url or '',
            'endpoint': endpoint or '',
            'params': params or {},
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        return hashlib.sha256(encoded).hexdigest()

    @api.model
    def get_cached(self, api_record, method, endpoint, params, body, include_expired=False):
        now = fields.Datetime.now()
        method = (method or 'GET').upper()
        cache_key = self.make_cache_key(api_record.base_url, method, endpoint, params or {}, body)
        domain = [
            ('api_id', '=', api_record.id),
            ('cache_key', '=', cache_key),
        ]
        if not include_expired:
            domain.append(('expires_at', '>', now))
        record = self.search(domain, limit=1)

        if not record and method == 'GET':
            legacy_key = self._legacy_cache_key(api_record.base_url, endpoint, params or {})
            legacy_domain = [
                ('api_id', '=', api_record.id),
                ('cache_key', '=', legacy_key),
            ]
            if not include_expired:
                legacy_domain.append(('expires_at', '>', now))
            record = self.search(legacy_domain, limit=1)
        if not record:
            return None
        record.sudo().write({'hits': record.hits + 1, 'last_hit_at': now})
        try:
            return json.loads(record.response_json) if record.response_json else None
        except Exception:
            return None

    @api.model
    def set_cached(self, api_record, method, endpoint, params, body, response_data, ok=True, status_code=None, error=None, ttl_seconds=0):
        now = fields.Datetime.now()
        method = (method or 'GET').upper()
        cache_key = self.make_cache_key(api_record.base_url, method, endpoint, params or {}, body)
        expires_at = now
        if ttl_seconds and ttl_seconds > 0:
            expires_at = fields.Datetime.add(now, seconds=ttl_seconds)

        record = self.search([
            ('api_id', '=', api_record.id),
            ('cache_key', '=', cache_key),
        ], limit=1)

        values = {
            'api_id': api_record.id,
            'cache_key': cache_key,
            'method': method,
            'endpoint': endpoint,
            'params_json': json.dumps(params or {}, ensure_ascii=False, default=str),
            'body_json': json.dumps(body or {}, ensure_ascii=False, default=str) if body is not None else False,
            'ok': bool(ok),
            'status_code': status_code or 0,
            'error': error or False,
            'response_json': json.dumps(response_data, ensure_ascii=False, default=str),
            'fetched_at': now,
            'expires_at': expires_at,
        }
        if record:
            record.sudo().write(values)
        else:
            self.sudo().create(values)
