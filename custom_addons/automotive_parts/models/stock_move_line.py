# -*- coding: utf-8 -*-
from odoo import api, models


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    def _refresh_automotive_sale_orders(self):
        moves = self.mapped('move_id')
        if moves:
            moves._refresh_automotive_sale_orders()

    @api.model_create_multi
    def create(self, vals_list):
        move_lines = super().create(vals_list)
        move_lines._refresh_automotive_sale_orders()
        return move_lines

    def write(self, vals):
        result = super().write(vals)
        self._refresh_automotive_sale_orders()
        return result

    def unlink(self):
        moves = self.mapped('move_id')
        result = super().unlink()
        if moves:
            moves._refresh_automotive_sale_orders()
        return result
