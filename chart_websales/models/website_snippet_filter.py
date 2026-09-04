# Part of Grupo Chart. See LICENSE file for full copyright and licensing details.

from odoo import api, models
from odoo.http import request


class WebsiteSnippetFilter(models.Model):
    """Inyecta tagline y precio "Desde $X MXN" a las tarjetas de categorías.

    Elegí extender `website.snippet.filter._prepare_category_list_data()`
    (vía heredar el modelo `website.snippet.filter`) en vez de forzar un
    override de controlador porque:
      1. Es el punto oficial donde el dynamic snippet de categorías arma sus
         `records` (el fetch del snippet llama a este método, ver
         `website_sale/data/data.xml`).
      2. No duplica lógica de rutas ni toca controllers de core.
    El template QWeb del snippet (heredado en snippet_category_inherit.xml)
    consume las claves `tagline` y `starting_price` que añadimos aquí.
    """

    _inherit = 'website.snippet.filter'

    @api.model
    def _prepare_category_list_data(self, parent_id=None):
        data = super()._prepare_category_list_data(parent_id=parent_id)
        Category = self.env['product.public.category'].sudo()
        for item in data:
            cat = Category.browse(item['id'])
            item['tagline'] = cat.chart_tagline or ''
            # Precio "Desde" = menor precio publicado de los templates de la categoría.
            templates = cat.product_tmpl_ids.filtered(
                lambda t: t.is_published and t.sale_ok and t.website_published
            )
            prices = []
            for tmpl in templates:
                variants = tmpl.product_variant_ids
                if variants:
                    prices.extend(variants.mapped('list_price'))
                else:
                    prices.append(tmpl.list_price)
            item['starting_price'] = min(prices) if prices else 0.0
            item['starting_price_label'] = (
                'Desde $%s MXN' % f"{item['starting_price']:,.0f}"
                if item['starting_price'] else ''
            )
        return data