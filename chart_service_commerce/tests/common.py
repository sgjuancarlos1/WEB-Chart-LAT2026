# -*- coding: utf-8 -*-
"""Fixture común: producto de prueba con precio y periodicidad EXPLÍCITOS.

No se publican condiciones comerciales del catálogo real: este producto solo
existe dentro de los tests (nunca en datos demo ni en post_init_hook).
"""
from odoo.addons.website_sale.tests.common import WebsiteSaleCommon


class ChartContractCommon(WebsiteSaleCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.chart_category = cls.env['product.public.category'].create(
            {'name': 'Chart Test Services'})
        cls.test_service = cls.env['product.template'].create({
            'name': 'SERVICIO DE PRUEBA Chart (test)',
            'type': 'service',
            'is_chart_service': True,
            'chart_service_periodicity': 'monthly',
            'list_price': 1500.0,
            'taxes_id': [(5, 0, 0)],
            'public_categ_ids': [(4, cls.chart_category.id)],
            'website_published': True,
        })
        cls.test_service_one_time = cls.env['product.template'].create({
            'name': 'IMPLEMENTACIÓN DE PRUEBA Chart (test)',
            'type': 'service',
            'is_chart_service': True,
            'chart_service_periodicity': 'none',
            'list_price': 5000.0,
            'taxes_id': [(5, 0, 0)],
            'website_published': True,
        })
        cls.test_variant_product = cls.env['product.template'].create({
            'name': 'SERVICIO CON VARIANTES Chart (test)',
            'type': 'service',
            'is_chart_service': True,
            'chart_service_periodicity': 'monthly',
            'list_price': 900.0,
            'taxes_id': [(5, 0, 0)],
            'website_published': True,
        })
        cls.attr_cadence = cls.env['product.attribute'].create({
            'name': 'Cadencia test',
            'create_variant': 'always',
            'value_ids': [
                (0, 0, {'name': 'Quincenal'}),
                (0, 0, {'name': 'Mensual'}),
            ],
        })
        cls.val_quincenal, cls.val_mensual = cls.attr_cadence.value_ids
        cls.attribute_line = cls.env['product.template.attribute.line'].create({
            'product_tmpl_id': cls.test_variant_product.id,
            'attribute_id': cls.attr_cadence.id,
            'value_ids': [(6, 0, [cls.val_quincenal.id, cls.val_mensual.id])],
        })
        cls.test_variant_product._create_variant_ids()
        cls.variant_quincenal = cls.env['product.product'].search([
            ('product_tmpl_id', '=', cls.test_variant_product.id),
            ('product_template_attribute_value_ids.product_attribute_value_id',
             '=', cls.val_quincenal.id),
        ], limit=1)
        cls.variant_mensual = cls.env['product.product'].search([
            ('product_tmpl_id', '=', cls.test_variant_product.id),
            ('product_template_attribute_value_ids.product_attribute_value_id',
             '=', cls.val_mensual.id),
        ], limit=1)

    def _make_order(self, partner, lines):
        """Crea el carrito nativo con las líneas dadas (variantes explícitas)."""
        order = self.env['sale.order'].create({
            'partner_id': partner.id,
            'website_id': self.website.id,
            'order_line': [
                (0, 0, {
                    'product_id': product.id,
                    'product_uom_qty': qty,
                })
                for product, qty in lines
            ],
        })
        return order

    def _portal_partner(self, name, email):
        user = self.env['res.users'].create({
            'name': name,
            'login': email,
            'email': email,
            'partner_id': self.env['res.partner'].create({
                'name': name, 'email': email,
                'phone': '+52 55 1234 5678',
                'city': 'Ciudad de México',
                'zip': '06600',
                'vat': 'XAXX010101000',
            }).id,
            'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])],
        })
        return user.partner_id
