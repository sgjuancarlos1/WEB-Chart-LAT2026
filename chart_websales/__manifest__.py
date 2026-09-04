{
    'name': 'Grupo Chart — Websales & Conversión E-commerce',
    'version': '19.0.1.1.0',
    'category': 'Website/eCommerce',
    'author': 'Grupo Chart',
    'website': 'https://chart.lat',
    'summary': 'Experiencia e-commerce, showcase de los 4 productos estrella y optimización de conversión.',
    'description': """
Grupo Chart — Websales & Conversión E-commerce
==============================================

Módulo especializado en la experiencia de e-commerce del Grupo Chart:
- Showcase de los 4 productos estrella en la homepage (herencia QWeb, sin tocar arch_db del builder).
- Fichas de producto enriquecidas: "¿Qué incluye?", "Ideal para ti si...", banner de confianza.
- Tarjetas de categorías con tagline + precio "Desde" (snippet dinámico).
- Badges visuales vía ribbons nativos y product.tag.
- Cross-sell vía alternative_products nativos.
""",
    'depends': [
        'website',
        'website_sale',
        'theme_cobalt',
        'web_unsplash',
    ],
    'data': [
        'data/product_tags_data.xml',
        'data/products_data.xml',
        'data/product_images.xml',
        'views/shop_inherit_views.xml',
        'views/product_page_inherit_views.xml',
        'views/snippet_category_inherit.xml',
        'views/homepage_showcase_views.xml',
        'views/testimonials_section.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'chart_websales/static/src/scss/chart_websales.scss',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}