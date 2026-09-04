# Part of Grupo Chart. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class ProductPublicCategory(models.Model):
    """Extensión mínima de categoría pública: tagline para tarjetas del snippet."""

    _inherit = 'product.public.category'

    chart_tagline = fields.Char(
        string="Tagline",
        translate=True,
        help="Frase de una línea mostrada en las tarjetas de categorías (snippet dinámico).",
    )