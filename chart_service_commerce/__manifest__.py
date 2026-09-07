{
    'name': 'Grupo Chart — Service Commerce & Billing',
    'version': '19.0.1.0.0',
    'category': 'Website/eCommerce',
    'author': 'Grupo Chart',
    'website': 'https://chart.lat',
    'summary': 'Service-based e-commerce platform with deferred payment, contracts, provisioning, and recurring billing.',
    'description': """
Grupo Chart — Service Commerce & Billing
==========================================

Complete service-based e-commerce platform for Chart solutions:
- Shopping cart with user authentication (registration/login during checkout)
- Deferred payment (no charging at confirmation, confirm order without payment)
- Service contract creation with email verification (72h activation threshold)
- 3-day provisioning pipeline (data import, API key generation, user setup, onboarding)
- Monthly billing automation with recurring invoices
- Customer portal extension (/my/service) for contract management and billing history
- Multi-tenant ACLs and record rules for portal security
- CFDI/Mexico localization compatibility

This module depends on chart_websales (frontend showcase) but is independent
for commerce logic, allowing reuse for future product offerings.
""",
    'depends': [
        'chart_websales',
        'sale',
        'account',
        'website_sale',
        'auth_signup',
        'portal',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/record_rules.xml',
        'data/ir_sequence_data.xml',
        'data/ir_cron_data.xml',
        'data/email_templates_data.xml',
        'views/service_contract_views.xml',
        'views/provisioning_task_views.xml',
        'views/checkout_views.xml',
        'views/portal_views.xml',
        'views/account_move_inherit.xml',
        'views/templates/email_verification.xml',
        'views/templates/activation_confirmed.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'chart_service_commerce/static/src/scss/checkout.scss',
            'chart_service_commerce/static/src/scss/portal.scss',
            'chart_service_commerce/static/src/js/checkout.js',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
