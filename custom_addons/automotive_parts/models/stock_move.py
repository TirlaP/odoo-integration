# -*- coding: utf-8 -*-
from odoo import models


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _get_automotive_sale_orders(self):
        sale_lines = self.env['sale.order.line']
        for move in self:
            sale_lines |= move.sale_line_id
            sale_lines |= move._get_sale_order_lines()
            sale_lines |= move.move_dest_ids.sale_line_id
            sale_lines |= move.move_orig_ids.sale_line_id
        return sale_lines.mapped('order_id').filtered(lambda order: order.exists())

    def _refresh_automotive_sale_orders(self):
        orders = self._get_automotive_sale_orders()
        if orders:
            orders._refresh_automotive_stock_state()
        return orders

    def _action_assign(self, force_qty=False):
        result = super()._action_assign(force_qty=force_qty)
        self._refresh_automotive_sale_orders()
        return result

    def _action_done(self, cancel_backorder=False):
        result = super()._action_done(cancel_backorder=cancel_backorder)
        (self | result)._refresh_automotive_sale_orders()
        return result

    def _action_cancel(self):
        orders = self._get_automotive_sale_orders()
        result = super()._action_cancel()
        if orders:
            orders._refresh_automotive_stock_state()
        return result
