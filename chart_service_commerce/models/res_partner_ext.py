from odoo import fields, models


class ResPartnerExt(models.Model):
    _inherit = 'res.partner'

    service_account_ids = fields.One2many(
        'chart.service.contract',
        'partner_id',
        string='Service Accounts',
        help='Service contracts for this partner'
    )
    default_service_product = fields.Many2one(
        'product.template',
        string='Default Service Product',
        help='Preferred solution (for quick reorder)'
    )
    service_metadata = fields.Json(
        string='Service Metadata',
        default='{}',
        help='Flexible storage for industry, size, preferences'
    )
