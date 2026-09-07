from odoo import fields, models


class SaleOrderExt(models.Model):
    _inherit = 'sale.order'

    service_contract_id = fields.Many2one(
        'chart.service.contract',
        string='Service Contract',
        ondelete='set null',
        help='Linked service contract from checkout'
    )
    is_service_order = fields.Boolean(
        string='Is Service Order',
        compute='_compute_is_service_order',
        store=False,
        help='True if order contains service products'
    )
    cart_session_id = fields.Char(
        string='Cart Session ID',
        help='UUID for deferred checkout session tracking'
    )

    def _compute_is_service_order(self):
        """Check if order contains service products."""
        for order in self:
            # Products from chart_websales are services
            is_service = any(
                line.product_id.type == 'service'
                for line in order.order_line
            )
            order.is_service_order = is_service
