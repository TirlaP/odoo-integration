# -*- coding: utf-8 -*-
from odoo import models


class AccountMove(models.Model):
    _inherit = 'account.move'

    def action_post(self):
        result = super().action_post()
        commercial_archive = self.env['commercial.document.archive']
        for move in self.filtered(lambda move: move.state == 'posted' and move.move_type in {'out_invoice', 'out_refund', 'out_receipt', 'in_invoice', 'in_refund'}):
            commercial_archive.sync_from_source_document(
                move,
                note=f'Automatically linked from posted accounting document {move.name or move.ref or move.id}.',
                archive=True,
            )
        return result
