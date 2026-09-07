from odoo import fields, models


class AccountMoveExt(models.Model):
    _inherit = 'account.move'

    service_contract_id = fields.Many2one(
        'chart.service.contract',
        string='Service Contract',
        ondelete='set null',
        help='Linked service contract (for recurring billing)'
    )
    is_service_invoice = fields.Boolean(
        string='Is Service Invoice',
        default=False,
        help='True if invoice is recurring billing from service contract'
    )
    recurring_id = fields.Many2one(
        'account.move',
        string='Previous Invoice',
        ondelete='set null',
        help='Previous invoice in recurring cycle'
    )

    def _get_service_invoice_lines(self):
        """Get lines related to service contract."""
        return self.invoice_line_ids.filtered(
            lambda l: l.service_contract_id
        )
