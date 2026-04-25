# -*- coding: utf-8 -*-
import html
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class AnafOAuthController(http.Controller):
    @http.route('/anaf/oauth/callback', type='http', auth='public', csrf=False, methods=['GET'])
    def anaf_oauth_callback(self, **params):
        state = (params.get('state') or '').strip()
        code = (params.get('code') or '').strip()
        error = (params.get('error') or '').strip()
        error_description = (params.get('error_description') or '').strip()

        config = request.env['anaf.efactura'].sudo().search([('oauth_state', '=', state)], limit=1) if state else False
        if not config:
            _logger.warning("ANAF OAuth callback rejected: unknown state=%s error=%s", state, error)
            return self._render_result(
                "ANAF OAuth callback rejected",
                "State was missing or did not match an active Odoo authorization request. Open OAuth Login again from Odoo.",
                status=400,
            )

        if error:
            config.sudo().write({
                'last_sync_message': f'ANAF OAuth denied: {error} {error_description}'.strip(),
            })
            _logger.warning(
                "ANAF OAuth denied for config id=%s state=%s error=%s description=%s",
                config.id,
                state,
                error,
                error_description,
            )
            return self._render_result(
                "ANAF OAuth denied",
                f"ANAF returned {error}. {error_description or 'Certificate was accepted by browser, but ANAF refused authorization.'}",
                status=403,
            )

        if not code:
            return self._render_result(
                "ANAF OAuth callback missing code",
                "ANAF did not return an authorization code. Open OAuth Login again from Odoo.",
                status=400,
            )

        config.sudo().write({'oauth_authorization_code': code})
        try:
            config.sudo()._exchange_authorization_code()
        except Exception as exc:
            _logger.exception("ANAF OAuth code exchange failed for config id=%s", config.id)
            config.sudo().write({'last_sync_message': f'ANAF OAuth code exchange failed: {exc}'})
            return self._render_result(
                "ANAF OAuth code received, exchange failed",
                str(exc),
                status=502,
            )

        return self._render_result(
            "ANAF OAuth connected",
            "Access and refresh tokens were saved in Odoo. You can close this tab and fetch invoices.",
        )

    def _render_result(self, title, message, status=200):
        body = f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <title>{html.escape(title)}</title>
    <style>
      body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 48px; color: #222; }}
      main {{ max-width: 760px; }}
      h1 {{ font-size: 28px; margin-bottom: 16px; }}
      p {{ font-size: 16px; line-height: 1.45; }}
    </style>
  </head>
  <body>
    <main>
      <h1>{html.escape(title)}</h1>
      <p>{html.escape(message)}</p>
    </main>
  </body>
</html>"""
        return request.make_response(body, headers=[('Content-Type', 'text/html; charset=utf-8')], status=status)
