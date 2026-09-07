# -*- coding: utf-8 -*-
"""Componentes económicos estructurados de un contrato.

El snapshot de texto (economic_terms_snapshot) sirve para lectura, no como
motor de cobro. Aquí se guardan los componentes estructurados congelados del
pedido: variante y línea origen, cantidad, precio aceptado, descuento, moneda,
impuestos y si el cargo es único o recurrente (con su periodicidad).

Una solución puede tener implementación (único) + mensualidad (recurrente):
cada línea de pedido se clasifica por separado según la periodicidad de su
producto. No se clasifica todo el contrato como único o recurrente.
"""
from odoo import fields, models


class ChartServiceEconomicComponent(models.Model):
    _name = 'chart.service.economic.component'
    _description = 'Componente económico de un contrato de servicio'
    _order = 'contract_id, sequence, id'

    contract_id = fields.Many2one(
        'chart.service.contract', string='Contrato', required=True,
        ondelete='cascade', index=True)
    sequence = fields.Integer(string='Orden', default=10)
    order_line_id = fields.Many2one(
        'sale.order.line', string='Línea de pedido origen', required=True,
        ondelete='restrict', index=True)
    product_id = fields.Many2one(
        'product.product', string='Variante', related='order_line_id.product_id',
        store=True, readonly=True)
    name = fields.Text(string='Descripción', related='order_line_id.name',
                       store=True, readonly=True)
    quantity = fields.Float(
        string='Cantidad', related='order_line_id.product_uom_qty', store=True,
        readonly=True)
    uom_id = fields.Many2one(
        'uom.uom', string='Unidad', related='order_line_id.product_uom_id',
        store=True, readonly=True)
    price_unit = fields.Float(
        string='Precio unitario aceptado', related='order_line_id.price_unit',
        store=True, readonly=True)
    discount = fields.Float(
        string='Descuento (%)', related='order_line_id.discount', store=True,
        readonly=True)
    price_subtotal = fields.Monetary(
        string='Subtotal', related='order_line_id.price_subtotal', store=True,
        readonly=True, currency_field='currency_id')
    price_total = fields.Monetary(
        string='Total', related='order_line_id.price_total', store=True,
        readonly=True, currency_field='currency_id')
    tax_ids = fields.Many2many(
        'account.tax', string='Impuestos', related='order_line_id.tax_ids',
        readonly=True)
    currency_id = fields.Many2one(
        'res.currency', string='Moneda', related='order_line_id.currency_id',
        store=True, readonly=True)
    # Clasificación del cargo: único o recurrente (según la periodicidad del
    # producto de la línea). Cada línea se clasifica por separado.
    charge_type = fields.Selection(
        selection=[('one_time', 'Cargo único'), ('recurring', 'Recurrente')],
        string='Tipo de cargo', required=True)
    periodicity = fields.Selection(
        selection=[
            ('none', 'Cargo único (sin recurrencia)'),
            ('monthly', 'Mensual'),
            ('quarterly', 'Trimestral'),
            ('annual', 'Anual'),
        ],
        string='Periodicidad', required=True, default='none')
    # Versión y fecha de aceptación de las condiciones (congeladas).
    terms_version = fields.Char(string='Versión de condiciones')
    terms_accepted_datetime = fields.Datetime(string='Condiciones aceptadas el')
