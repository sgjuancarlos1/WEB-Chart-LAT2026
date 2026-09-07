import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class ServiceActivation(models.Model):
    _name = 'chart.service.activation'
    _description = 'Service Activation — Tracking and Status'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    contract_id = fields.Many2one(
        'chart.service.contract',
        string='Service Contract',
        required=True,
        ondelete='cascade',
        help='Parent service contract'
    )
    
    activation_status = fields.Selection([
        ('pending', 'Pending'),
        ('email_verified', 'Email Verified'),
        ('data_complete', 'Data Complete'),
        ('activated', 'Activated'),
        ('failed', 'Failed'),
    ], string='Activation Status', default='pending', tracking=True)
    
    data_completeness_percent = fields.Integer(
        string='Data Completeness %',
        compute='_compute_data_completeness',
        help='Percentage of required fields filled'
    )
    
    verification_method = fields.Selection([
        ('email', 'Email Link'),
        ('manual', 'Manual Admin Approval'),
    ], string='Verification Method', default='email')
    
    attempt_count = fields.Integer(
        string='Activation Attempts',
        default=0,
        help='Number of activation attempts'
    )
    
    last_attempt_date = fields.Datetime(
        string='Last Attempt Date',
        help='When last activation was attempted'
    )
    
    notes = fields.Text(
        string='Activation Notes',
        help='Log of activation checks and failures'
    )

    @api.depends('contract_id.partner_id')
    def _compute_data_completeness(self):
        """Calculate percentage of required fields completed."""
        for activation in self:
            partner = activation.contract_id.partner_id
            required_fields = ['name', 'email', 'phone', 'street', 'city', 'country_id']
            filled = sum(1 for f in required_fields if getattr(partner, f, None))
            activation.data_completeness_percent = int((filled / len(required_fields)) * 100)

    def action_check_activation_readiness(self):
        """Check if contract is ready for activation."""
        for activation in self:
            contract = activation.contract_id
            
            activation.attempt_count += 1
            activation.last_attempt_date = fields.Datetime.now()
            
            checks = {
                'email_verified': contract.email_verified,
                'data_complete': contract._check_data_complete(),
                'created_72h_ago': (fields.Datetime.now() - contract.create_date).days >= 3,
            }
            
            ready = all(checks.values())
            activation.notes = f"Activation checks: {checks} → Ready: {ready}"
            
            if ready and activation.activation_status != 'activated':
                activation.activation_status = 'activated'
                contract.state = 'active'
                contract.activation_date = fields.Date.today()
                contract.action_generate_provisioning_tasks()
                _logger.info("Contract %s activated after readiness check", contract.name)

    def action_request_manual_verification(self):
        """Send message to admin for manual approval."""
        self.verification_method = 'manual'
        self.message_post(
            body=_('Manual verification requested for contract activation.'),
            message_type='notification',
        )
