# Part of Grupo Chart. See LICENSE file for full copyright and licensing details.

from odoo.addons.website_sale.controllers.main import WebsiteSale


class ChartWebsiteSale(WebsiteSale):
    """Extensión reservada del controlador de e-commerce.

    Por ahora no se requieren rutas custom: las fichas de producto usan la
    URL nativa /shop/<slug> generada por website_sale, el selector de
    variantes actualiza precio vía la ruta nativa /shop/cart/update_json
    y el cross-sell usa alternative_products nativo.
    Si más adelante se requiere un landing por producto, se agrega un
    método con @http.route() aquí sin tocar el controlador base.
    """