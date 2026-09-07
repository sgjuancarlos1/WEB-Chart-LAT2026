# -*- coding: utf-8 -*-
{
    'name': 'Grupo Chart — Contratación de Servicios',
    'version': '19.0.2.0.0',
    'category': 'Sales/eCommerce',
    'author': 'Grupo Chart',
    'website': 'https://chart.lat',
    'summary': 'Contratación de servicios sobre carrito/pedido nativo, portal "Mis '
               'soluciones", preparación y activación asistida. Sin facturación real.',
    'description': """
Grupo Chart — Contratación de Servicios (reescrito tras revisión del plan)
==========================================================================
Decisiones corregidas vs. la propuesta anterior:
- Reutiliza registro, sesión, carrito y sale.order NATIVOS (auth_signup + website_sale).
  No hay autenticación paralela ni segundo pedido si el carrito ya tiene uno.
- El contrato se construye desde las LÍNEAS del pedido (variantes product.product,
  cantidades y precios congelados), no desde un Many2many de product.template.
- Varios servicios por pedido con preparación/activación independiente por contrato.
- Activación = flujo real: contratación → verificación/datos → preparación →
  comprobación de entorno → activación manual asistida. Las 72 h son un objetivo
  informativo (activation_target_date), NUNCA un disparador automático.
- Sin facturación en esta entrega: modelos de períodos facturables calculados por
  meses calendario desde el ancla de activación, pero sin emitir/timbrar/enviar.
- Seguridad: ACL de portal + reglas de propiedad; nada de secretos en metadata;
  sin post_init_hook de datos demo; datos de prueba solo en tests/ y tools/.
""",
    'depends': [
        'website_sale',
        'portal',
        'auth_signup',
        'sale_management',
    ],
    'data': [
        'security/chart_service_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'views/product_template_views.xml',
        'views/service_contract_views.xml',
        'views/portal_templates.xml',
        'views/website_sale_templates.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'chart_service_commerce/static/src/scss/checkout.scss',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
