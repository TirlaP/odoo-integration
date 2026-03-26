# -*- coding: utf-8 -*-
import base64
import json
import logging
import os
import uuid
import zipfile
from datetime import timedelta
from io import BytesIO
from urllib.parse import urlencode
from xml.etree import ElementTree

import requests

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ANAFEFactura(models.Model):
    """ANAF e-Factura Integration"""
    _name = 'anaf.efactura'
    _description = 'ANAF e-Factura Integration'

    _ANAF_AUTHORIZE_URL = 'https://logincert.anaf.ro/anaf-oauth2/v1/authorize'
    _ANAF_TOKEN_URL = 'https://logincert.anaf.ro/anaf-oauth2/v1/token'
    _ANAF_API_BASE = 'https://api.anaf.ro'

    name = fields.Char('Name', default='ANAF e-Factura')
    active = fields.Boolean(default=True)
    environment = fields.Selection(
        [('test', 'Test'), ('prod', 'Production')],
        string='Environment',
        default=lambda self: self._default_environment(),
        required=True,
    )
    use_oauth = fields.Boolean(
        string='Use OAuth2 (Recommended)',
        default=True,
        help='When enabled, use ANAF OAuth2 token exchange/refresh flow.',
    )

    api_url = fields.Char('API URL', default=lambda self: self._default_api_url())
    api_token = fields.Char(
        'Legacy API Token',
        help='Deprecated fallback bearer token. Use OAuth2 access/refresh tokens instead.',
        password=True,
    )

    oauth_authorize_url = fields.Char(
        'OAuth Authorize URL',
        default=lambda self: self._env('ANAF_OAUTH_AUTHORIZE_URL', self._ANAF_AUTHORIZE_URL),
    )
    oauth_token_url = fields.Char(
        'OAuth Token URL',
        default=lambda self: self._env('ANAF_OAUTH_TOKEN_URL', self._ANAF_TOKEN_URL),
    )
    oauth_client_id = fields.Char(
        'OAuth Client ID',
        default=lambda self: self._env('ANAF_OAUTH_CLIENT_ID'),
    )
    oauth_client_secret = fields.Char(
        'OAuth Client Secret',
        password=True,
        default=lambda self: self._env('ANAF_OAUTH_CLIENT_SECRET'),
    )
    oauth_redirect_uri = fields.Char(
        'OAuth Redirect URI',
        default=lambda self: self._env('ANAF_OAUTH_REDIRECT_URI'),
    )
    oauth_token_content_type = fields.Selection(
        [('jwt', 'jwt')],
        string='token_content_type',
        default='jwt',
        required=True,
    )
    oauth_authorization_code = fields.Char('Authorization Code', copy=False)
    oauth_state = fields.Char('OAuth State', copy=False)
    access_token = fields.Char('Access Token', password=True, copy=False)
    refresh_token = fields.Char('Refresh Token', password=True, copy=False)
    token_expires_at = fields.Datetime('Access Token Expires At')
    refresh_expires_at = fields.Datetime('Refresh Token Expires At')

    fetch_days = fields.Integer('Fetch Range (days)', default=7, help='Allowed by ANAF: 1..60')
    fetch_filter = fields.Selection(
        [('P', 'Primite (P)'), ('T', 'Trimise (T)'), ('E', 'Erori (E)'), ('R', 'Mesaje cumpărător (R)')],
        string='Fetch Filter',
        default='P',
    )
    last_sync_at = fields.Datetime('Last Sync At', readonly=True)
    last_sync_message = fields.Text('Last Sync Message', readonly=True)
    last_fetch_count = fields.Integer('Last Fetch Count', readonly=True)

    # Configuration
    cui_company = fields.Char(
        'CUI Companie',
        help='Your company CUI for ANAF',
        default=lambda self: self._normalize_cui(self._env('ANAF_EFACTURA_CUI')),
    )

    @api.model
    def _env(self, key, default=None):
        return os.getenv(key, default)

    @api.model
    def _default_environment(self):
        env = (self._env('ANAF_EFACTURA_ENV', 'prod') or 'prod').strip().lower()
        return env if env in {'test', 'prod'} else 'prod'

    @api.model
    def _default_api_url(self):
        return f"{self._ANAF_API_BASE}/{self._default_environment()}/FCTEL/rest"

    @api.onchange('environment')
    def _onchange_environment(self):
        for rec in self:
            rec.api_url = f"{self._ANAF_API_BASE}/{rec.environment}/FCTEL/rest"

    def action_load_from_env(self):
        for rec in self:
            vals = {}
            mapping = {
                'ANAF_EFACTURA_ENV': 'environment',
                'ANAF_EFACTURA_CUI': 'cui_company',
                'ANAF_OAUTH_AUTHORIZE_URL': 'oauth_authorize_url',
                'ANAF_OAUTH_TOKEN_URL': 'oauth_token_url',
                'ANAF_OAUTH_CLIENT_ID': 'oauth_client_id',
                'ANAF_OAUTH_CLIENT_SECRET': 'oauth_client_secret',
                'ANAF_OAUTH_REDIRECT_URI': 'oauth_redirect_uri',
                'ANAF_EFACTURA_ACCESS_TOKEN': 'access_token',
                'ANAF_EFACTURA_REFRESH_TOKEN': 'refresh_token',
            }
            for env_key, field_name in mapping.items():
                env_val = self._env(env_key)
                if env_val is None or env_val == '':
                    continue
                if field_name == 'environment':
                    env_val = env_val.strip().lower()
                    if env_val not in {'test', 'prod'}:
                        continue
                if field_name == 'cui_company':
                    env_val = self._normalize_cui(env_val)
                vals[field_name] = env_val

            if vals:
                rec.write(vals)
                rec._onchange_environment()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Loaded from Environment',
                'message': 'ANAF configuration fields were refreshed from environment variables.',
                'type': 'success',
            },
        }

    def _get_headers(self):
        """Get API headers."""
        token = self._get_bearer_token()
        return {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }

    def _get_bearer_token(self):
        self.ensure_one()
        if self.use_oauth:
            if not self.access_token or self._is_access_token_expired():
                self._refresh_access_token(raise_if_missing=True)
            if not self.access_token:
                raise UserError('Missing ANAF access token. Use OAuth2 flow to generate one.')
            return self.access_token
        if not self.api_token:
            raise UserError('Please configure ANAF API token first!')
        return self.api_token

    def _is_access_token_expired(self):
        self.ensure_one()
        if not self.token_expires_at:
            return False
        expires_at = fields.Datetime.to_datetime(self.token_expires_at)
        now = fields.Datetime.to_datetime(fields.Datetime.now())
        return expires_at <= (now + timedelta(minutes=1))

    def _api_base_url(self):
        self.ensure_one()
        if self.api_url:
            return self.api_url.rstrip('/')
        return f"{self._ANAF_API_BASE}/{self.environment}/FCTEL/rest"

    def _build_basic_auth_header(self):
        self.ensure_one()
        if not self.oauth_client_id or not self.oauth_client_secret:
            raise UserError('Set OAuth Client ID and Client Secret first.')
        raw = f"{self.oauth_client_id}:{self.oauth_client_secret}".encode('utf-8')
        token = base64.b64encode(raw).decode('ascii')
        return {'Authorization': f'Basic {token}'}

    def action_open_authorize_url(self):
        self.ensure_one()
        if not self.oauth_client_id or not self.oauth_redirect_uri:
            raise UserError('Set OAuth Client ID and OAuth Redirect URI first.')

        state = str(uuid.uuid4())
        self.write({'oauth_state': state})
        query = urlencode({
            'response_type': 'code',
            'client_id': self.oauth_client_id,
            'redirect_uri': self.oauth_redirect_uri,
            'token_content_type': self.oauth_token_content_type or 'jwt',
            'state': state,
        })
        url = f"{(self.oauth_authorize_url or self._ANAF_AUTHORIZE_URL).rstrip('?')}?{query}"
        return {'type': 'ir.actions.act_url', 'url': url, 'target': 'new'}

    def _store_oauth_tokens(self, payload):
        self.ensure_one()
        expires_in = int(payload.get('expires_in') or 90 * 24 * 3600)
        refresh_expires_in = int(payload.get('refresh_expires_in') or 365 * 24 * 3600)
        now = fields.Datetime.to_datetime(fields.Datetime.now())

        vals = {
            'access_token': payload.get('access_token') or self.access_token,
            'refresh_token': payload.get('refresh_token') or self.refresh_token,
            'token_expires_at': now + timedelta(seconds=expires_in),
            'refresh_expires_at': now + timedelta(seconds=refresh_expires_in),
            'oauth_authorization_code': False,
        }
        self.write(vals)

    def action_exchange_authorization_code(self):
        for rec in self:
            rec._exchange_authorization_code()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'OAuth Code Exchanged',
                'message': 'Access and refresh tokens were obtained successfully.',
                'type': 'success',
            },
        }

    def _exchange_authorization_code(self):
        self.ensure_one()
        if not self.oauth_authorization_code:
            raise UserError('Paste the OAuth authorization code first.')
        if not self.oauth_redirect_uri:
            raise UserError('Set OAuth Redirect URI first.')

        payload = {
            'grant_type': 'authorization_code',
            'code': self.oauth_authorization_code.strip(),
            # ANAF docs are OAuth2-standard (`code`), but some environments
            # return validation errors mentioning `auth_code`; send both for
            # compatibility.
            'auth_code': self.oauth_authorization_code.strip(),
            'redirect_uri': self.oauth_redirect_uri.strip(),
            'token_content_type': self.oauth_token_content_type or 'jwt',
        }
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        headers.update(self._build_basic_auth_header())
        response = requests.post(
            (self.oauth_token_url or self._ANAF_TOKEN_URL).strip(),
            data=payload,
            headers=headers,
            timeout=60,
        )
        if response.status_code >= 400:
            raise UserError(f'ANAF OAuth code exchange failed: {response.text}')
        data = response.json()
        if not data.get('access_token'):
            raise UserError(f'ANAF OAuth response has no access_token: {response.text}')
        self._store_oauth_tokens(data)

    def action_refresh_access_token(self):
        for rec in self:
            rec._refresh_access_token(raise_if_missing=True)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Access Token Refreshed',
                'message': 'A new access token was obtained from ANAF.',
                'type': 'success',
            },
        }

    def _refresh_access_token(self, raise_if_missing=False):
        self.ensure_one()
        if not self.refresh_token:
            if raise_if_missing:
                raise UserError('Missing refresh token. Run OAuth authorization code flow first.')
            return False

        payload = {
            'grant_type': 'refresh_token',
            'refresh_token': self.refresh_token,
            'token_content_type': self.oauth_token_content_type or 'jwt',
        }
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        headers.update(self._build_basic_auth_header())
        response = requests.post(
            (self.oauth_token_url or self._ANAF_TOKEN_URL).strip(),
            data=payload,
            headers=headers,
            timeout=60,
        )
        if response.status_code >= 400:
            if raise_if_missing:
                raise UserError(f'ANAF OAuth refresh failed: {response.text}')
            return False
        data = response.json()
        if data.get('access_token'):
            self._store_oauth_tokens(data)
            return True
        return False

    @api.model
    def _normalize_cui(self, value):
        if not value:
            return False
        digits = ''.join(ch for ch in str(value) if ch.isdigit())
        if not digits:
            return False
        return digits.lstrip('0') or digits

    @api.model
    def _to_float(self, value):
        if value in (None, False, ''):
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        normalized = str(value).replace(',', '.').strip()
        try:
            return float(normalized)
        except (TypeError, ValueError):
            return 0.0

    @api.model
    def _to_date(self, value):
        if not value:
            return False
        try:
            return fields.Date.to_date(value)
        except Exception:
            return False

    @api.model
    def _find_nested_text(self, root, names):
        node = root
        for name in names:
            next_node = None
            for child in list(node):
                tag = child.tag.rsplit('}', 1)[-1]
                if tag == name:
                    next_node = child
                    break
            if not next_node:
                return False
            node = next_node
        text = (node.text or '').strip()
        return text or False

    @api.model
    def _find_direct_text(self, root, local_name):
        for child in list(root):
            tag = child.tag.rsplit('}', 1)[-1]
            if tag != local_name:
                continue
            text = (child.text or '').strip()
            if text:
                return text
        return False

    @api.model
    def _parse_ubl_xml(self, xml_payload):
        try:
            root = ElementTree.fromstring(xml_payload.encode('utf-8') if isinstance(xml_payload, str) else xml_payload)
        except Exception as exc:
            _logger.warning("Failed to parse UBL XML: %s", exc)
            return {}

        invoice_number = self._find_direct_text(root, 'ID')
        issue_date = self._find_direct_text(root, 'IssueDate')
        supplier_cui = (
            self._find_nested_text(root, ['AccountingSupplierParty', 'Party', 'PartyTaxScheme', 'CompanyID'])
            or self._find_nested_text(root, ['AccountingSupplierParty', 'Party', 'PartyLegalEntity', 'CompanyID'])
        )
        customer_cui = (
            self._find_nested_text(root, ['AccountingCustomerParty', 'Party', 'PartyTaxScheme', 'CompanyID'])
            or self._find_nested_text(root, ['AccountingCustomerParty', 'Party', 'PartyLegalEntity', 'CompanyID'])
        )
        total_node = self._find_nested_text(root, ['LegalMonetaryTotal', 'PayableAmount'])
        currency_node = self._find_direct_text(root, 'DocumentCurrencyCode')

        lines = []
        for line_node in root.iter():
            if line_node.tag.rsplit('}', 1)[-1] != 'InvoiceLine':
                continue
            description = self._find_nested_text(line_node, ['Item', 'Name']) or 'ANAF invoice line'
            quantity = self._to_float(self._find_nested_text(line_node, ['InvoicedQuantity']) or 0.0)
            price_unit = self._to_float(self._find_nested_text(line_node, ['Price', 'PriceAmount']) or 0.0)
            line_total = self._to_float(self._find_nested_text(line_node, ['LineExtensionAmount']) or 0.0)
            lines.append({
                'description': description,
                'quantity': quantity or 1.0,
                'price_unit': price_unit,
                'line_total': line_total,
            })

        parsed = {
            'invoice_number': invoice_number or False,
            'invoice_date': issue_date or False,
            'supplier_cui': supplier_cui,
            'customer_cui': customer_cui,
            'total_amount': self._to_float(total_node),
            'currency_code': currency_node or False,
            'lines': lines,
        }
        return parsed

    @api.model
    def _find_supplier(self, supplier_cui):
        normalized = self._normalize_cui(supplier_cui)
        if not normalized:
            return self.env['res.partner']

        partner = self.env['res.partner'].search([('cui', '=', normalized)], limit=1)
        if partner:
            return partner

        # Fallback when CUI is stored in VAT as "RO{cui}".
        return self.env['res.partner'].search([('vat', 'ilike', normalized)], limit=1)

    @api.model
    def _extract_invoice_payload(self, invoice_data):
        raw = invoice_data
        if isinstance(invoice_data, str):
            try:
                invoice_data = json.loads(invoice_data)
            except Exception:
                invoice_data = {'raw': invoice_data}
        if not isinstance(invoice_data, dict):
            invoice_data = {}

        payload_blob = invoice_data.get('payload')
        if isinstance(payload_blob, str):
            try:
                payload_blob = json.loads(payload_blob)
            except Exception:
                payload_blob = {'raw_payload': payload_blob}
        if not isinstance(payload_blob, dict):
            payload_blob = {}

        xml_payload = (
            invoice_data.get('xml')
            or invoice_data.get('ubl_xml')
            or invoice_data.get('document_xml')
            or payload_blob.get('xml')
            or payload_blob.get('ubl_xml')
            or payload_blob.get('document_xml')
        )
        parsed_xml = self._parse_ubl_xml(xml_payload) if xml_payload else {}

        supplier_cui = (
            parsed_xml.get('supplier_cui')
            or invoice_data.get('supplier_cui')
            or payload_blob.get('supplier_cui')
            or invoice_data.get('supplierCui')
            or payload_blob.get('supplierCui')
        )
        invoice_number = (
            parsed_xml.get('invoice_number')
            or invoice_data.get('invoice_number')
            or payload_blob.get('invoice_number')
            or invoice_data.get('invoiceNumber')
            or payload_blob.get('invoiceNumber')
        )
        invoice_date = (
            parsed_xml.get('invoice_date')
            or invoice_data.get('invoice_date')
            or payload_blob.get('invoice_date')
            or invoice_data.get('invoiceDate')
            or payload_blob.get('invoiceDate')
        )
        total_amount = (
            parsed_xml.get('total_amount')
            if parsed_xml.get('total_amount')
            else (
                invoice_data.get('total_amount')
                or payload_blob.get('total_amount')
                or invoice_data.get('totalAmount')
                or payload_blob.get('totalAmount')
            )
        )
        currency_code = (
            parsed_xml.get('currency_code')
            or invoice_data.get('currency')
            or payload_blob.get('currency')
            or invoice_data.get('currency_code')
            or payload_blob.get('currency_code')
        )
        external_id = (
            invoice_data.get('id')
            or payload_blob.get('id')
            or invoice_data.get('message_id')
            or payload_blob.get('message_id')
            or invoice_data.get('messageId')
            or payload_blob.get('messageId')
            or invoice_data.get('document_id')
            or payload_blob.get('document_id')
        )

        return {
            'external_id': str(external_id) if external_id else False,
            'supplier_cui': self._normalize_cui(supplier_cui),
            'invoice_number': (invoice_number or '').strip() or False,
            'invoice_date': self._to_date(invoice_date),
            'total_amount': self._to_float(total_amount),
            'currency_code': (currency_code or '').strip().upper() or False,
            'lines': parsed_xml.get('lines') or [],
            'raw_payload': raw,
            'xml_payload': xml_payload,
        }

    @api.model
    def _extract_messages_list(self, data):
        if not data:
            return []
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            for key in ('mesaje', 'messages', 'lista', 'listaMesaje'):
                value = data.get(key)
                if isinstance(value, list):
                    return [x for x in value if isinstance(x, dict)]
            # Some ANAF responses can contain message data directly in root.
            if any(k in data for k in ('id', 'id_solicitare', 'tip', 'data_creare')):
                return [data]
        return []

    @api.model
    def _is_invoice_xml(self, xml_payload):
        try:
            root = ElementTree.fromstring(xml_payload.encode('utf-8') if isinstance(xml_payload, str) else xml_payload)
        except Exception:
            return False
        local_name = root.tag.rsplit('}', 1)[-1]
        return local_name in {'Invoice', 'CreditNote'}

    @api.model
    def _extract_invoice_xmls_from_zip(self, zip_bytes):
        xml_files = []
        with zipfile.ZipFile(BytesIO(zip_bytes), 'r') as archive:
            for name in archive.namelist():
                if not name.lower().endswith('.xml'):
                    continue
                try:
                    xml_text = archive.read(name).decode('utf-8', errors='ignore')
                except Exception:
                    continue
                if not xml_text.strip():
                    continue
                xml_files.append(xml_text)
        invoice_xmls = [x for x in xml_files if self._is_invoice_xml(x)]
        return invoice_xmls or xml_files[:1]

    def _create_message_attachment(self, message_id, binary_payload):
        self.ensure_one()
        attachment_name = f'anaf_message_{message_id}.zip'
        return self.env['ir.attachment'].create({
            'name': attachment_name,
            'type': 'binary',
            'mimetype': 'application/zip',
            'datas': base64.b64encode(binary_payload),
            'res_model': self._name,
            'res_id': self.id,
        })

    def _download_message_payload(self, message_id):
        self.ensure_one()
        endpoint = f"{self._api_base_url()}/descarcare"
        response = requests.get(
            endpoint,
            headers=self._get_headers(),
            params={'id': message_id},
            timeout=120,
        )
        response.raise_for_status()
        content = response.content or b''
        if not content:
            return [], False

        # ANAF descărcare returns zip with XMLs; keep a fallback for direct XML.
        if content[:2] == b'PK':
            xml_payloads = self._extract_invoice_xmls_from_zip(content)
            attachment = self._create_message_attachment(message_id, content)
            return xml_payloads, attachment

        text_payload = content.decode('utf-8', errors='ignore')
        if text_payload.strip().startswith('<'):
            return [text_payload], False
        return [], False

    def fetch_invoices(self, days_back=None, message_filter=None):
        """Fetch e-Factura messages from ANAF and create ingest jobs."""
        total_created = 0
        for rec in self:
            days = int(days_back or rec.fetch_days or 7)
            days = min(max(days, 1), 60)
            filter_code = (message_filter or rec.fetch_filter or 'P').strip().upper()
            params = {
                'zile': days,
                'cif': rec._normalize_cui(rec.cui_company),
            }
            if not params['cif']:
                raise UserError('Set company CUI before fetching ANAF messages.')
            if filter_code in {'P', 'T', 'E', 'R'}:
                params['filtru'] = filter_code

            endpoint = f"{rec._api_base_url()}/listaMesajeFactura"
            try:
                response = requests.get(endpoint, headers=rec._get_headers(), params=params, timeout=60)
                response.raise_for_status()
                data = response.json()
            except requests.exceptions.RequestException as exc:
                rec.write({
                    'last_sync_at': fields.Datetime.now(),
                    'last_sync_message': str(exc),
                    'last_fetch_count': 0,
                })
                _logger.error("ANAF list fetch failed: %s", exc)
                raise UserError(f'ANAF API list fetch failed: {exc}')

            messages = rec._extract_messages_list(data)
            created_jobs = 0
            for msg in messages:
                message_id = (
                    msg.get('id')
                    or msg.get('id_solicitare')
                    or msg.get('idSolicitare')
                )
                if not message_id:
                    continue
                message_id = str(message_id)
                try:
                    xml_payloads, attachment = rec._download_message_payload(message_id)
                    if not xml_payloads:
                        continue
                    for xml_payload in xml_payloads:
                        invoice_data = {
                            'id': message_id,
                            'xml': xml_payload,
                            'payload': msg,
                        }
                        job, created = rec._process_invoice(invoice_data)
                        if attachment and not job.attachment_id:
                            job.write({'attachment_id': attachment.id})
                        if created:
                            created_jobs += 1
                except requests.exceptions.RequestException as exc:
                    _logger.warning("ANAF message download failed for id=%s: %s", message_id, exc)
                    continue
                except Exception as exc:  # noqa: BLE001 - keep sync resilient to malformed payloads.
                    _logger.warning("ANAF message processing failed for id=%s: %s", message_id, exc)
                    continue

            rec.write({
                'last_sync_at': fields.Datetime.now(),
                'last_sync_message': f'Fetched {len(messages)} messages, created {created_jobs} jobs.',
                'last_fetch_count': created_jobs,
            })
            _logger.info(
                "ANAF fetch complete for config %s: messages=%s created_jobs=%s",
                rec.id,
                len(messages),
                created_jobs,
            )
            total_created += created_jobs

        return total_created

    def _process_invoice(self, invoice_data):
        """Parse ANAF payload and enqueue an idempotent ingest job."""
        parsed = self._extract_invoice_payload(invoice_data)
        _logger.info(
            "Processing ANAF invoice payload external_id=%s invoice=%s",
            parsed.get('external_id'),
            parsed.get('invoice_number'),
        )

        supplier = self._find_supplier(parsed.get('supplier_cui'))
        currency = self.env.company.currency_id
        if parsed.get('currency_code'):
            currency = self.env['res.currency'].search([('name', '=', parsed['currency_code'])], limit=1) or currency

        payload = {
            'parsed': {
                'external_id': parsed.get('external_id'),
                'supplier_cui': parsed.get('supplier_cui'),
                'invoice_number': parsed.get('invoice_number'),
                'invoice_date': parsed.get('invoice_date').isoformat() if parsed.get('invoice_date') else False,
                'total_amount': parsed.get('total_amount'),
                'currency_code': parsed.get('currency_code'),
                'lines': parsed.get('lines') or [],
            },
            'raw': parsed.get('raw_payload'),
        }

        job, created = self.env['invoice.ingest.job'].upsert_invoice_job(
            source='anaf',
            external_id=parsed.get('external_id'),
            partner_id=supplier.id if supplier else False,
            invoice_number=parsed.get('invoice_number'),
            invoice_date=parsed.get('invoice_date'),
            amount_total=parsed.get('total_amount'),
            currency_id=currency.id if currency else False,
            payload=payload,
        )

        if not supplier:
            warning = f'Unknown supplier CUI: {parsed.get("supplier_cui") or "missing"}'
            job.write({'state': 'needs_review', 'error': warning})
            _logger.warning(warning)
            return job, created

        if not parsed.get('invoice_number'):
            job.write({'state': 'needs_review', 'error': 'Missing invoice number in ANAF payload.'})
            return job, created

        if not job.account_move_id:
            job.action_create_draft_vendor_bill()
        return job, created

    @api.model
    def cron_fetch_invoices(self):
        """Scheduled action to fetch invoices from ANAF"""
        for anaf_config in self.search([('active', '=', True)]):
            try:
                count = anaf_config.fetch_invoices(days_back=anaf_config.fetch_days)
                _logger.info("Fetched %s ANAF invoices for config id=%s", count, anaf_config.id)
            except Exception as exc:  # noqa: BLE001 - cron should continue for other configs.
                _logger.error("Error fetching ANAF invoices for config id=%s: %s", anaf_config.id, exc)


class ANAFInvoiceWizard(models.TransientModel):
    """Wizard to link ANAF invoice to reception"""
    _name = 'anaf.invoice.wizard'
    _description = 'ANAF Invoice Linking Wizard'

    picking_id = fields.Many2one('stock.picking', 'Reception', required=True)
    partner_id = fields.Many2one('res.partner', 'Supplier')
    invoice_number = fields.Char('Invoice Number / Reference')
    invoice_id = fields.Many2one(
        'account.move',
        'Vendor Bill',
        domain="[('move_type', '=', 'in_invoice')]",
    )

    @api.onchange('invoice_number', 'partner_id')
    def _onchange_invoice_number(self):
        if not self.invoice_number:
            return
        domain = [('move_type', '=', 'in_invoice')]
        if self.partner_id:
            domain.append(('partner_id', '=', self.partner_id.id))
        domain += ['|', ('ref', 'ilike', self.invoice_number), ('name', 'ilike', self.invoice_number)]
        match = self.env['account.move'].search(domain, order='id desc', limit=1)
        if match:
            self.invoice_id = match.id

    def action_fetch_and_link(self):
        """Link an existing vendor bill to this reception (ANAF/OCR/manual source)."""
        self.ensure_one()

        invoice = self.invoice_id
        if not invoice and self.invoice_number:
            domain = [('move_type', '=', 'in_invoice')]
            if self.partner_id:
                domain.append(('partner_id', '=', self.partner_id.id))
            domain += ['|', ('ref', 'ilike', self.invoice_number), ('name', 'ilike', self.invoice_number)]
            invoice = self.env['account.move'].search(domain, order='id desc', limit=1)

        if not invoice:
            raise UserError('Select a Vendor Bill (or type an invoice reference that exists).')

        vals = {
            'supplier_invoice_id': invoice.id,
            'supplier_invoice_number': invoice.ref or invoice.name,
            'supplier_invoice_date': invoice.invoice_date,
        }
        if invoice.partner_id and not self.picking_id.partner_id:
            vals['partner_id'] = invoice.partner_id.id
        self.picking_id.write(vals)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Invoice Linked',
                'message': f'Linked vendor bill {invoice.display_name} to reception',
                'type': 'success',
            }
        }
