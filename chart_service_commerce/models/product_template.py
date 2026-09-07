# -*- coding: utf-8 -*-
"""Marca un product.template como "contratable" y define su periodicidad.

No se añade ningún precio aquí: el importe contratado es SIEMPRE el de la
línea del sale.order (variante + cantidad + precio vigente en el pedido).
"""
from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    is_chart_service = fields.Boolean(
        string='Contratable como servicio',
        default=False,
        help='Permite contratar este producto desde la tienda sin pago inmediato. '
             'El contrato se crea desde las líneas del pedido.',
    )
    chart_service_periodicity = fields.Selection(
        selection=[
            ('none', 'Cargo único (sin recurrencia)'),
            ('monthly', 'Mensual'),
            ('quarterly', 'Trimestral'),
            ('annual', 'Anual'),
        ],
        string='Periodicidad del servicio',
        default='none',
        help='Define cómo se generarán los períodos facturables FUTUROS. '
             'En esta entrega no se emite ninguna factura.',
    )
    chart_service_note = fields.Text(
        string='Nota de preparación',
        help='Instrucciones internas para el equipo de preparación de este servicio.',
    )
