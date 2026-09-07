import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class ProvisioningTask(models.Model):
    _name = 'chart.provisioning.task'
    _description = 'Provisioning Task — Service Setup Pipeline'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'due_date, id'

    # =========================================================================
    # FIELDS: Task configuration
    # =========================================================================
    name = fields.Char(
        string='Task Name',
        required=True,
        help='e.g., "Day 1: Data Import"'
    )
    contract_id = fields.Many2one(
        'chart.service.contract',
        string='Service Contract',
        required=True,
        ondelete='cascade',
        help='Parent service contract'
    )
    task_type = fields.Selection([
        ('data_import', 'Data Import'),
        ('api_key_gen', 'API Key Generation'),
        ('user_setup', 'User Setup'),
        ('onboarding_call', 'Onboarding Call'),
    ], string='Task Type', required=True, help='Type of provisioning task')

    # =========================================================================
    # STATE & WORKFLOW
    # =========================================================================
    state = fields.Selection([
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ], string='State', default='pending', tracking=True)

    assigned_to = fields.Many2one(
        'res.users',
        string='Assigned To',
        help='User responsible for task execution'
    )

    # =========================================================================
    # DATES
    # =========================================================================
    due_date = fields.Date(
        string='Due Date',
        required=True,
        help='When task should be completed'
    )
    completion_date = fields.Date(
        string='Completion Date',
        readonly=True,
        help='When task was actually completed'
    )

    # =========================================================================
    # NOTES & DOCUMENTATION
    # =========================================================================
    notes = fields.Text(
        string='Notes',
        help='Task details, execution notes, errors'
    )
    attachment_ids = fields.Many2many(
        'ir.attachment',
        relation='chart_provisioning_task_attachment_rel',
        column1='task_id',
        column2='attachment_id',
        string='Attachments',
        help='Related files (API docs, imports, etc.)'
    )

    # =========================================================================
    # ACTIONS
    # =========================================================================
    def action_mark_in_progress(self):
        """Transition task to in_progress."""
        self.state = 'in_progress'
        self.message_post(body=_('Task marked as in progress.'))

    def action_mark_completed(self):
        """Transition task to completed."""
        self.state = 'completed'
        self.completion_date = fields.Date.today()
        self.message_post(body=_('Task completed.'))

    def action_mark_failed(self):
        """Transition task to failed."""
        self.state = 'failed'
        self.message_post(body=_('Task marked as failed.'))
        # Notify admin
        self.contract_id.message_post(
            body=_('Provisioning task %s failed for contract %s') % (self.name, self.contract_id.name),
            message_type='comment',
        )

    # =========================================================================
    # CRON JOB: Process pending tasks
    # =========================================================================
    @api.model
    def _cron_process_pending_tasks(self):
        """Scheduled action to process pending provisioning tasks."""
        pending_tasks = self.search([('state', '=', 'pending')])
        
        for task in pending_tasks:
            if task.due_date <= fields.Date.today():
                try:
                    task._execute_task()
                    task.action_mark_completed()
                except Exception as e:
                    _logger.error("Failed to execute task %s: %s", task.name, str(e))
                    task.state = 'failed'
                    task.notes = str(e)

    def _execute_task(self):
        """Route task to appropriate handler by task_type."""
        handlers = {
            'data_import': self._task_data_import,
            'api_key_gen': self._task_api_key_gen,
            'user_setup': self._task_user_setup,
            'onboarding_call': self._task_onboarding_call,
        }
        
        handler = handlers.get(self.task_type)
        if handler:
            handler()
        else:
            raise ValueError(f"Unknown task type: {self.task_type}")

    # =========================================================================
    # TASK HANDLERS: Implement per task type
    # =========================================================================
    def _task_data_import(self):
        """Execute data import task (placeholder)."""
        contract = self.contract_id
        # In production, call external API or queue import job
        # For now, just mark metadata
        contract.metadata['data_import_status'] = 'completed'
        self.notes = 'Data import completed (placeholder)'
        _logger.info("Data import task completed for contract %s", contract.name)

    def _task_api_key_gen(self):
        """Generate API key for service integration."""
        contract = self.contract_id
        # In production, generate cryptographic key and store securely
        api_key = self.env['ir.config_parameter'].sudo().get_param(
            'chart_service_commerce.api_key_secret'
        ) or 'demo_key_' + contract.name
        contract.metadata['api_key'] = api_key
        self.notes = 'API key generated'
        _logger.info("API key generated for contract %s", contract.name)

    def _task_user_setup(self):
        """Create user accounts in the service (placeholder)."""
        contract = self.contract_id
        # In production, call provisioning API
        contract.metadata['users_created'] = 1
        self.notes = 'User account setup completed'
        _logger.info("User setup task completed for contract %s", contract.name)

    def _task_onboarding_call(self):
        """Queue onboarding call for CSM assignment."""
        contract = self.contract_id
        # Assign to admin or CSM group
        admin_user = self.env.ref('base.user_admin', raise_if_not_found=False)
        if admin_user:
            self.assigned_to = admin_user.id
        self.notes = 'Onboarding call scheduled'
        _logger.info("Onboarding call queued for contract %s", contract.name)
