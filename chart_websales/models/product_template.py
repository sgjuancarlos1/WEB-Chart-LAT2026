# Part of Grupo Chart. See LICENSE file for full copyright and licensing details.

import base64
import logging
import struct
import zlib

from odoo import _, models

_logger = logging.getLogger(__name__)


def _png_placeholder(hex_color, width=1200, height=700):
    """Genera un PNG de color sólido (sin Pillow) para placeholders visuales."""
    color = (hex_color or '#1a3c6e').lstrip('#')
    r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    row = b'\x00' + bytes((r, g, b)) * width  # filter None + RGB row
    raw = row * height

    def chunk(tag, data):
        out = struct.pack('>I', len(data)) + tag + data
        return out + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)

    img = b'\x89PNG\r\n\x1a\n'
    img += chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
    img += chunk(b'IDAT', zlib.compress(raw))
    img += chunk(b'IEND', b'')
    return base64.b64encode(img)


# ===========================================================================
# FASE 3: Datos de los 4 productos estrella (atributos, variantes, precios).
# Se crean en post_init_hook para usar el mecanismo oficial de variantes
# (_create_variant_ids) sin inflar el XML con cientos de registros.
# ===========================================================================
PRODUCT_SPECS = [
    {
        'xmlid': 'product_erp360',
        'attrs': [
            {
                'name': 'Alcance de Implementación',
                'values': [
                    ('PyME Starter', 0.0),
                    ('Gestión Total', 13500.0),
                    ('Corporativo', 36500.0),
                ],
            },
            {
                'name': 'Póliza de Soporte & Cloud AWS',
                'values': [
                    ('Sin Póliza', 0.0),
                    ('Póliza Esencial', 4500.0),
                    ('Póliza Total', 9500.0),
                ],
            },
        ],
    },
    {
        'xmlid': 'product_linda',
        'attrs': [
            {
                'name': 'Nivel de Automatización IA',
                'values': [
                    ('LINDA Starter', 0.0),
                    ('LINDA Pro', 8100.0),
                    ('LINDA Enterprise', 21100.0),
                ],
            },
        ],
    },
    {
        'xmlid': 'product_seo',
        'attrs': [
            {
                'name': 'Plan de Ventas Digitales',
                'values': [
                    ('Tienda Starter', 0.0),
                    ('Tienda Pro', 7500.0),
                    ('Tienda Full Growth', 18500.0),
                ],
            },
        ],
    },
    {
        'xmlid': 'product_finanzas',
        'attrs': [
            {
                'name': 'Nivel de Asesoría Financiera',
                'values': [
                    ('Diagnóstico de Tesorería', 0.0),
                    ('Gestión Mensual de Portafolio', 0.0),
                    ('Estrategia Corporativa Integral', 28000.0),
                ],
            },
        ],
    },
]

# (substring del nombre de categoría, tagline, color placeholder)
CATEGORY_DATA = [
    ('ERP 360', 'ERP Odoo, AWS, soporte y procesos: tu operación en orden.', '#1a3c6e'),
    ('L.I.N.D.A', 'Agentes de IA que automatizan y deciden con tus datos.', '#4b2e83'),
    ('SEO', 'Tráfico, tienda en línea y conversión que sí se mide.', '#1e5631'),
    ('CFO Finanzas', 'Trading, bonos y mercado global con analítica clara.', '#c9a24b'),
]

# Color de la imagen placeholder por producto (clave = sufijo del xmlid)
PRODUCT_IMAGE_COLORS = {
    'erp360': '#1a3c6e',
    'linda': '#4b2e83',
    'seo': '#1e5631',
    'finanzas': '#c9a24b',
}


def post_init_hook(env):
    """Crea atributos, variantes, precios, categorías, taglines e imágenes."""
    env.cr.execute("SAVEPOINT chart_websales_post_init")

    ProductAttribute = env['product.attribute']
    ProductAttributeValue = env['product.attribute.value']
    Ptal = env['product.template.attribute.line']

    for spec in PRODUCT_SPECS:
        template = env.ref('chart_websales.%s' % spec['xmlid'])
        for attr_spec in spec['attrs']:
            attribute = ProductAttribute.create({
                'name': attr_spec['name'],
                'display_type': 'radio',
                'create_variant': 'always',
            })
            values = ProductAttributeValue.create([
                {'name': name, 'attribute_id': attribute.id}
                for name, _extra in attr_spec['values']
            ])
            price_map = dict(attr_spec['values'])
            ptal = Ptal.create({
                'product_tmpl_id': template.id,
                'attribute_id': attribute.id,
                'value_ids': [(6, 0, values.ids)],
            })
            # Los PTAV (product.template.attribute.value) se generan solos;
            # ahí vive el price_extra por valor.
            for ptav in ptal.product_template_value_ids:
                ptav.price_extra = price_map[ptav.product_attribute_value_id.name]
            _logger.info("chart_websales: atributo '%s' creado para %s (%s valores)",
                         attribute.name, template.name, len(values))

        # Genera las variantes con el mecanismo oficial.
        # NOTA: NO se escribe variant.list_price — en Odoo 19 es un campo
        # related al template; el precio por variante lo resuelve
        # product.price_extra (suma de price_extra de los PTAV).
        template._create_variant_ids()

        _logger.info("chart_websales: %s variantes creadas para %s (base %s, extra hasta %s)",
                     len(template.product_variant_ids), template.name, template.list_price,
                     max(template.product_variant_ids.mapped('price_extra') or [0.0]))

    # ---- Categorías públicas, taglines e imágenes placeholder ----
    Category = env['product.public.category'].sudo()
    for keyword, tagline, color in CATEGORY_DATA:
        category = Category.search([('name', 'ilike', keyword)], limit=1)
        if not category:
            _logger.warning("chart_websales: categoría '%s' no encontrada", keyword)
            continue
        category.chart_tagline = _(tagline)
        if not category.cover_image:
            category.cover_image = _png_placeholder(color)

    # Asocia cada producto a su categoría y le pone imagen placeholder
    CATEGORY_LINK = [
        ('product_erp360', 'ERP 360'),
        ('product_linda', 'L.I.N.D.A'),
        ('product_seo', 'SEO'),
        ('product_finanzas', 'CFO Finanzas'),
    ]
    for xmlid, keyword in CATEGORY_LINK:
        tmpl = env.ref('chart_websales.%s' % xmlid, raise_if_not_found=False)
        category = Category.search([('name', 'ilike', keyword)], limit=1)
        if tmpl and category:
            tmpl.public_categ_ids = [(4, category.id)]
        prod_color = PRODUCT_IMAGE_COLORS.get(xmlid.replace('product_', ''), '#1a3c6e')
        if tmpl and not tmpl.image_1920:
            tmpl.image_1920 = _png_placeholder(prod_color)

    # ---- Cross-sell (alternative_product_ids) — aquí el orden no importa ----
    ALTERNATIVES = {
        'product_erp360': ['product_connector_whatsapp', 'product_migracion_db', 'product_capacitacion_adicional'],
        'product_linda': ['product_erp360', 'product_seo', 'product_finanzas'],
        'product_seo': ['product_erp360', 'product_linda', 'product_finanzas'],
        'product_finanzas': ['product_erp360', 'product_linda', 'product_seo'],
    }
    for main_xmlid, alt_xmlids in ALTERNATIVES.items():
        main_tmpl = env.ref('chart_websales.%s' % main_xmlid, raise_if_not_found=False)
        if not main_tmpl:
            continue
        alt_ids = []
        for alt_xmlid in alt_xmlids:
            alt_tmpl = env.ref('chart_websales.%s' % alt_xmlid, raise_if_not_found=False)
            if alt_tmpl:
                alt_ids.append(alt_tmpl.id)
        if alt_ids:
            main_tmpl.alternative_product_ids = [(6, 0, alt_ids)]

    # Imagen de los 3 complementos (addons de ERP 360)
    for xmlid in ['product_connector_whatsapp', 'product_migracion_db', 'product_capacitacion_adicional']:
        tmpl = env.ref('chart_websales.%s' % xmlid, raise_if_not_found=False)
        if tmpl and not tmpl.image_1920:
            tmpl.image_1920 = _png_placeholder('#1a3c6e')

    env.cr.execute("RELEASE SAVEPOINT chart_websales_post_init")