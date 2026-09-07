from odoo import fields, models


class AccountMoveLineExt(models.Model):
    _inherit = 'account.move.line'

    service_contract_id = fields.Many2one(
        'chart.service.contract',
        string='Service Contract',
        ondelete='set null',
        help='Associated service contract for this line item'
    )
