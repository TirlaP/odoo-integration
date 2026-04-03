# -*- coding: utf-8 -*-
from odoo import api, models


class AutomotiveMailMaintenance(models.Model):
    _name = 'automotive.mail.maintenance'
    _description = 'Automotive Mail Maintenance'

    @api.model
    def _cleanup_model_links(self, model_name, res_ids, cleanup_activities=False):
        res_ids = [res_id for res_id in set(res_ids or []) if res_id]
        if not res_ids:
            return {
                'messages': 0,
                'activities': 0,
            }

        message_model = self.env['mail.message'].sudo()
        activity_model = self.env['mail.activity'].sudo()
        messages = message_model.search([
            ('model', '=', model_name),
            ('res_id', 'in', res_ids),
        ])
        activities = self.env['mail.activity']
        if cleanup_activities:
            activities = activity_model.search([
                ('res_model', '=', model_name),
                ('res_id', 'in', res_ids),
            ])

        message_count = len(messages)
        activity_count = len(activities)
        if activities:
            activities.unlink()
        if messages:
            messages.unlink()

        return {
            'messages': message_count,
            'activities': activity_count,
        }

    @api.model
    def run_orphan_cleanup(self):
        summary = []
        sources = [
            ('activity', 'res_model', 'res_id', 'mail_activity'),
            ('message', 'model', 'res_id', 'mail_message'),
        ]
        for source, model_field, id_field, table_name in sources:
            self.env.cr.execute(
                f"""
                SELECT {model_field}, array_agg(DISTINCT {id_field})
                FROM {table_name}
                WHERE {model_field} IS NOT NULL
                  AND {id_field} IS NOT NULL
                GROUP BY {model_field}
                """
            )
            for model_name, res_ids in self.env.cr.fetchall():
                if model_name not in self.env:
                    continue
                existing_ids = set(self.env[model_name].sudo().browse(res_ids).exists().ids)
                missing_ids = [res_id for res_id in res_ids if res_id not in existing_ids]
                if not missing_ids:
                    continue
                counts = self._cleanup_model_links(
                    model_name,
                    missing_ids,
                    cleanup_activities=(source == 'activity'),
                )
                summary.append({
                    'source': source,
                    'model': model_name,
                    'missing_ids': missing_ids,
                    'messages': counts['messages'],
                    'activities': counts['activities'],
                })
        return summary


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def unlink(self):
        self.env['automotive.mail.maintenance']._cleanup_model_links(
            'purchase.order',
            self.ids,
            cleanup_activities=True,
        )
        return super().unlink()


class ProductProduct(models.Model):
    _inherit = 'product.product'

    def unlink(self):
        self.env['automotive.mail.maintenance']._cleanup_model_links(
            'product.product',
            self.ids,
            cleanup_activities=True,
        )
        return super().unlink()
