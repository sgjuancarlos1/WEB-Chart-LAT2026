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
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ChartServiceEconomicComponent(models.Model):
    _name = 'chart.service.economic.component'
    _description = 'Componente económico de un contrato de servicio'
    _order = 'contract_id, sequence, id'

    contract_id = fields.Many2one(
        'chart.service.contract', string='Contrato', required=True,
        ondelete='cascade', index=True)
    company_id = fields.Many2one(
        related='contract_id.company_id', store=True, index=True,
        string='Compañía')
    sequence = fields.Integer(string='Orden', default=10)
    order_line_id = fields.Many2one(
        'sale.order.line', string='Línea de pedido origen (procedencia)',
        required=True, ondelete='restrict', index=True)

    # SNAPSHOT INMUTABLE: valores congelados al momento de contratar. NO son
    # campos related: si la línea de pedido origen cambiara después de la
    # confirmación, el componente NO cambia (así se demuestra en tests).
    product_id = fields.Many2one(
        'product.product', string='Variante', readonly=True)
    name = fields.Text(string='Descripción', readonly=True)
    quantity = fields.Float(string='Cantidad', readonly=True)
    uom_id = fields.Many2one('uom.uom', string='Unidad', readonly=True)
    price_unit = fields.Float(
        string='Precio unitario aceptado', readonly=True)
    discount = fields.Float(string='Descuento (%)', readonly=True)
    price_subtotal = fields.Monetary(
        string='Subtotal', readonly=True, currency_field='currency_id')
    price_total = fields.Monetary(
        string='Total', readonly=True, currency_field='currency_id')
    tax_ids = fields.Many2many(
        'account.tax', string='Impuestos', readonly=True)
    currency_id = fields.Many2one(
        'res.currency', string='Moneda', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        """Congela los valores de la línea origen SOLO en la creación."""
        Line = self.env['sale.order.line']
        for vals in vals_list:
            line = Line.browse(vals['order_line_id'])
            vals.setdefault('product_id', line.product_id.id)
            vals.setdefault('name', line.name)
            vals.setdefault('quantity', line.product_uom_qty)
            vals.setdefault('uom_id', line.product_uom_id.id)
            vals.setdefault('price_unit', line.price_unit)
            vals.setdefault('discount', line.discount)
            vals.setdefault('price_subtotal', line.price_subtotal)
            vals.setdefault('price_total', line.price_total)
            vals.setdefault('tax_ids', [(6, 0, line.tax_ids.ids)])
            vals.setdefault('currency_id', line.currency_id.id)
        return super().create(vals_list)

    def write(self, vals):
        """Los valores congelados no se reescriben: el snapshot es inmutable."""
        immutable = {'product_id', 'name', 'quantity', 'uom_id', 'price_unit',
                     'discount', 'price_subtotal', 'price_total', 'tax_ids',
                     'currency_id', 'order_line_id'}
        if immutable.intersection(vals):
            raise ValidationError(_(
                "Los componentes económicos son un snapshot congelado en la "
                "contratación: no se editan. Crea un contrato nuevo si cambian "
                "las condiciones."))
        return super().write(vals)
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
