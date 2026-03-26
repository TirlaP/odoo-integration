# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request


class AutomotiveFaviconController(http.Controller):
    @http.route(
        ['/favicon.ico'],
        type='http',
        auth='public',
        website=False,
        multilang=False,
        sitemap=False,
        readonly=True,
    )
    def favicon(self, **kwargs):
        # Some local/prod databases run without the website module installed.
        # In that case the core website favicon route can 500 while trying to
        # resolve website-specific data. Fall back to the static web favicon.
        return request.redirect('/web/static/img/favicon.ico', code=301)
