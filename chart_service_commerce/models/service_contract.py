import logging
import uuid
from datetime import timedelta

from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import ValidationError, UserError

_logger = logging.getLogger(__name__)


class ServiceContract(models.Model):
    _name = 'chart.service.contract'
    _description = 'Service Contract — Chart Solutions'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    # =========================================================================
    # FIELDS: Core contract data
    # =========================================================================
    name = fields.Char(
        string='Contract Number',
        readonly=True,
        copy=False,
        help='Auto-generated: CT-2026-00001'
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Customer',
        required=True,
        ondelete='cascade',
        help='Service contract partner/customer'
    )
    sale_order_id = fields.Many2one(
        'sale.order',
        string='Sale Order',
        ondelete='set null',
        help='Linked sale order from checkout'
    )
    product_ids = fields.Many2many(
        'product.template',
        relation='chart_service_contract_product_rel',
        column1='contract_id',
        column2='product_id',
        string='Products/Solutions',
        help='Services purchased'
    )

    # =========================================================================
    # STATE & WORKFLOW
    # =========================================================================
    state = fields.Selection([
        ('draft', 'Draft'),
        ('pending_activation', 'Pending Activation'),
        ('active', 'Active'),
        ('canceled', 'Canceled'),
    ], string='State', default='draft', tracking=True)

    activation_date = fields.Date(
        string='Activation Date',
        help='Date contract activated (Day 3 of provisioning)'
    )
    expiry_date = fields.Date(
        string='Expiry Date',
        help='Contract expiration (for trial/limited plans)'
    )

    # =========================================================================
    # EMAIL VERIFICATION
    # =========================================================================
    email_verified = fields.Boolean(
        string='Email Verified',
        default=False,
        help='Customer verified email address via link'
    )
    email_verification_token = fields.Char(
        string='Email Verification Token',
        readonly=True,
        copy=False,
        help='UUID token for email verification link'
    )
    email_verified_date = fields.Date(
        string='Email Verified Date',
        readonly=True,
        help='Date email was verified'
    )

    # =========================================================================
    # BILLING CONFIGURATION
    # =========================================================================
    billing_cycle = fields.Selection([
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly'),
        ('annual', 'Annual'),
    ], string='Billing Cycle', default='monthly', help='Recurring invoice frequency')

    next_invoice_date = fields.Date(
        string='Next Invoice Date',
        help='Next recurring invoice generation date'
    )

    # =========================================================================
    # RELATIONSHIPS
    # =========================================================================
    provisioning_task_ids = fields.One2many(
        'chart.provisioning.task',
        'contract_id',
        string='Provisioning Tasks',
        help='3-day activation pipeline'
    )
    invoice_ids = fields.Many2many(
        'account.move',
        relation='chart_service_contract_invoice_rel',
        column1='contract_id',
        column2='invoice_id',
        string='Invoices',
        help='Associated customer invoices'
    )

    # =========================================================================
    # FLEXIBLE METADATA
    # =========================================================================
    metadata = fields.Json(
        string='Metadata',
        default='{}',
        help='Flexible JSON storage for product attributes, SKUs, API keys, etc.'
    )

    # =========================================================================
    # COMPUTED FIELDS
    # =========================================================================
    @api.depends('state')
    def _compute_status_label(self):
        """Status label for dashboard display."""
        labels = {
            'draft': 'Pending Confirmation',
            'pending_activation': 'Awaiting Verification',
            'active': 'Active',
            'canceled': 'Canceled',
        }
        for contract in self:
            contract.status_label = labels.get(contract.state, '')

    status_label = fields.Char(
        string='Status Label',
        compute='_compute_status_label',
        store=False
    )

    # =========================================================================
    # MODEL LIFECYCLE
    # =========================================================================
    @api.model_create_multi
    def create(self, vals_list):
        """Generate sequence on creation."""
        for vals in vals_list:
            if not vals.get('name'):
                vals['name'] = self.env['ir.sequence'].next_by_code('chart.service.contract')
            if not vals.get('email_verification_token'):
                vals['email_verification_token'] = str(uuid.uuid4())
        return super().create(vals_list)

    # =========================================================================
    # KEY ACTIONS
    # =========================================================================
    def action_send_verification_email(self):
        """Send email verification link to customer."""
        for contract in self:
            if not contract.partner_id.email:
                raise ValidationError(
                    _('Customer email is required for verification link.')
                )
            template = self.env.ref('chart_service_commerce.email_template_service_verification')
            email_values = {
                'email_to': contract.partner_id.email,
                'email_from': self.env.company.email,
            }
            template.send_mail(contract.id, email_values=email_values, force_send=True)
            _logger.info("Verification email sent to %s for contract %s",
                        contract.partner_id.email, contract.name)

    def action_verify_email(self, token):
        """Verify email by token and transition to pending_activation -> active."""
        contract = self.search([('email_verification_token', '=', token)], limit=1)
        if not contract:
            raise ValidationError(_('Invalid verification token.'))
        
        contract.email_verified = True
        contract.email_verified_date = fields.Date.today()
        contract.state = 'pending_activation'
        
        _logger.info("Email verified for contract %s", contract.name)
        return contract

    def action_generate_provisioning_tasks(self):
        """Generate 3-day provisioning task pipeline on activation."""
        ProvisioningTask = self.env['chart.provisioning.task']
        
        for contract in self:
            if contract.state != 'active':
                continue
                
            base_date = contract.activation_date or fields.Date.today()
            
            task_specs = [
                (_('Day 1: Data Import'), 'data_import', base_date + timedelta(days=1)),
                (_('Day 1: API Key Generation'), 'api_key_gen', base_date + timedelta(days=1)),
                (_('Day 2: User Setup'), 'user_setup', base_date + timedelta(days=2)),
                (_('Day 3: Onboarding Call'), 'onboarding_call', base_date + timedelta(days=3)),
            ]
            
            for name, task_type, due_date in task_specs:
                ProvisioningTask.create({
                    'name': name,
                    'contract_id': contract.id,
                    'task_type': task_type,
                    'due_date': due_date,
                    'state': 'pending',
                })
                _logger.info("Provisioning task '%s' created for %s", name, contract.name)

    def action_create_invoice(self):
        """Generate monthly invoice with service line items."""
        for contract in self:
            if contract.state != 'active':
                continue
            
            partner = contract.partner_id
            company = self.env.company
            
            move_vals = {
                'move_type': 'out_invoice',
                'partner_id': partner.id,
                'company_id': company.id,
                'invoice_date': fields.Date.today(),
                'service_contract_id': contract.id,
                'is_service_invoice': True,
            }
            
            # Add line for each product
            line_vals = []
            for product in contract.product_ids:
                # Get minimum variant price or template price
                if product.product_variant_ids:
                    price = min(product.product_variant_ids.mapped('list_price'))
                else:
                    price = product.list_price
                
                line_vals.append((0, 0, {
                    'product_id': product.id,
                    'name': product.name,
                    'quantity': 1,
                    'price_unit': price,
                }))
            
            move_vals['invoice_line_ids'] = line_vals
            invoice = self.env['account.move'].create(move_vals)
            
            # Link to previous invoice (recurrence)
            if contract.invoice_ids:
                invoice.recurring_id = contract.invoice_ids[-1].id
            
            contract.invoice_ids = [(4, invoice.id)]
            
            # Schedule next invoice
            next_date = fields.Date.today()
            if contract.billing_cycle == 'monthly':
                next_date += timedelta(days=30)
            elif contract.billing_cycle == 'quarterly':
                next_date += timedelta(days=91)
            elif contract.billing_cycle == 'annual':
                next_date += timedelta(days=365)
            
            contract.next_invoice_date = next_date
            
            _logger.info("Invoice %s created for contract %s, next due %s",
                        invoice.name, contract.name, next_date)
            return invoice

    # =========================================================================
    # CRON JOBS & AUTOMATION
    # =========================================================================
    @api.model
    def _cron_check_pending_activation(self):
        """Check contracts in pending_activation state and auto-activate if ready."""
        pending = self.search([('state', '=', 'pending_activation')])
        
        for contract in pending:
            data_complete = contract._check_data_complete()
            email_verified = contract.email_verified
            created_days_ago = (fields.Datetime.now() - contract.create_date).days
            
            if data_complete and email_verified and created_days_ago >= 3:
                contract.state = 'active'
                contract.activation_date = fields.Date.today()
                contract.action_generate_provisioning_tasks()
                contract.message_post(
                    body=_('Service contract activated automatically after 72h verification period.')
                )
                _logger.info("Contract %s activated after 72h", contract.name)

    @api.model
    def _cron_monthly_billing(self):
        """Generate recurring invoices for active contracts."""
        today = fields.Date.today()
        active_contracts = self.search([
            ('state', '=', 'active'),
            ('next_invoice_date', '<=', today),
        ])
        
        for contract in active_contracts:
            try:
                contract.action_create_invoice()
            except Exception as e:
                _logger.error("Failed to create invoice for contract %s: %s",
                             contract.name, str(e))
                contract.message_post(
                    body=_('Error creating monthly invoice: %s') % str(e),
                    message_type='comment',
                )

    # =========================================================================
    # VALIDATION HELPERS
    # =========================================================================
    def _check_data_complete(self):
        """Verify partner has all required fields for activation."""
        partner = self.partner_id
        required_fields = ['name', 'email', 'phone', 'street', 'city', 'country_id']
        
        for field in required_fields:
            if not getattr(partner, field, None):
                return False
        return True


def post_init_hook(env):
    """Post-install hook to set up default data."""
    _logger.info("chart_service_commerce: Post-init hook executed")
    # Placeholder for future demo data, sequences, etc.
    pass
