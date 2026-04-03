# -*- coding: utf-8 -*-

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPaymentAllocationAndPickingCleanup(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env['res.partner'].create({
            'name': 'Allocation Customer',
            'customer_rank': 1,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Allocation Product',
            'list_price': 100.0,
        })

    def _create_sale_order(self, unit_price=100.0):
        order = self.env['sale.order'].create({
            'partner_id': self.customer.id,
        })
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.product.id,
            'name': self.product.display_name,
            'product_uom_qty': 1.0,
            'price_unit': unit_price,
        })
        return order

    def _create_customer_payment(self, amount=100.0):
        return self.env['account.payment'].create({
            'amount': amount,
            'date': fields.Date.today(),
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': self.customer.id,
            'currency_id': self.env.company.currency_id.id,
        })

    def test_payment_allocation_create_suggests_positive_amount(self):
        order = self._create_sale_order(unit_price=60.0)
        payment = self._create_customer_payment(amount=100.0)

        allocation = self.env['automotive.payment.allocation'].create({
            'company_id': self.env.company.id,
            'payment_id': payment.id,
            'sale_order_id': order.id,
            'amount': 0.0,
        })

        self.assertEqual(allocation.amount, order.amount_total)

    def test_payment_allocation_rejects_non_positive_amount_when_nothing_is_left(self):
        order = self._create_sale_order(unit_price=60.0)
        payment = self._create_customer_payment(amount=100.0)
        self.env['automotive.payment.allocation'].create({
            'company_id': self.env.company.id,
            'payment_id': payment.id,
            'sale_order_id': order.id,
            'amount': order.amount_total,
        })

        with self.assertRaises(ValidationError):
            self.env['automotive.payment.allocation'].create({
                'company_id': self.env.company.id,
                'payment_id': payment.id,
                'sale_order_id': order.id,
                'amount': 0.0,
            })

    def test_unlink_stock_picking_cleans_chatter_messages(self):
        order = self._create_sale_order(unit_price=40.0)
        picking_type = self.env.ref('stock.picking_type_out')
        picking = self.env['stock.picking'].create({
            'partner_id': self.customer.id,
            'sale_id': order.id,
            'picking_type_id': picking_type.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
        })
        picking.message_post(body='Cleanup test message')
        message_ids = self.env['mail.message'].search([
            ('model', '=', 'stock.picking'),
            ('res_id', '=', picking.id),
        ]).ids

        self.assertTrue(message_ids)

        picking.unlink()

        orphan_messages = self.env['mail.message'].search([
            ('id', 'in', message_ids),
        ])
        self.assertFalse(orphan_messages)

    def test_unlink_sale_order_cleans_chatter_messages_and_activities(self):
        order = self.env['sale.order'].create({
            'partner_id': self.customer.id,
        })
        order.message_post(body='Cleanup test order message')
        activity = self.env['mail.activity'].create({
            'res_model_id': self.env['ir.model']._get_id('sale.order'),
            'res_id': order.id,
            'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
            'summary': 'Cleanup activity',
            'user_id': self.env.user.id,
            'date_deadline': fields.Date.today(),
        })
        message_ids = self.env['mail.message'].search([
            ('model', '=', 'sale.order'),
            ('res_id', '=', order.id),
        ]).ids

        self.assertTrue(message_ids)
        self.assertTrue(activity.exists())

        order.unlink()

        orphan_messages = self.env['mail.message'].search([
            ('id', 'in', message_ids),
        ])
        orphan_activities = self.env['mail.activity'].search([
            ('id', '=', activity.id),
        ])
        self.assertFalse(orphan_messages)
        self.assertFalse(orphan_activities)

    def test_unlink_purchase_order_cleans_chatter_messages_and_activities(self):
        order = self.env['purchase.order'].create({
            'partner_id': self.customer.id,
        })
        order.message_post(body='Cleanup test purchase order message')
        activity = self.env['mail.activity'].create({
            'res_model_id': self.env['ir.model']._get_id('purchase.order'),
            'res_id': order.id,
            'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
            'summary': 'Cleanup purchase activity',
            'user_id': self.env.user.id,
            'date_deadline': fields.Date.today(),
        })
        message_ids = self.env['mail.message'].search([
            ('model', '=', 'purchase.order'),
            ('res_id', '=', order.id),
        ]).ids

        self.assertTrue(message_ids)
        self.assertTrue(activity.exists())

        order.unlink()

        orphan_messages = self.env['mail.message'].search([
            ('id', 'in', message_ids),
        ])
        orphan_activities = self.env['mail.activity'].search([
            ('id', '=', activity.id),
        ])
        self.assertFalse(orphan_messages)
        self.assertFalse(orphan_activities)

    def test_unlink_product_cleans_chatter_messages(self):
        product = self.env['product.product'].create({
            'name': 'Cleanup Product',
            'list_price': 10.0,
        })
        product.message_post(body='Cleanup test product message')
        message_ids = self.env['mail.message'].search([
            ('model', '=', 'product.product'),
            ('res_id', '=', product.id),
        ]).ids

        self.assertTrue(message_ids)

        product.unlink()

        orphan_messages = self.env['mail.message'].search([
            ('id', 'in', message_ids),
        ])
        self.assertFalse(orphan_messages)
