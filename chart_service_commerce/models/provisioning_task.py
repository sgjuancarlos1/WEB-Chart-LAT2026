# -*- coding: utf-8 -*-
"""Tarea de preparación de un contrato.

Corregido vs. la propuesta anterior:
- NINGUNA tarea se auto-completa ni simula trabajo (no hay _task_api_key_gen,
  no se guarda ninguna clave en metadata del contrato).
- Una tarea pasa a 'done' SOLO cuando una persona autorizada la verifica y la
  marca manualmente, dejando evidencia (fecha + responsable + nota).
- El campo 'code' clasifica el tipo de paso; no ejecuta nada por sí mismo.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ChartProvisioningTask(models.Model):
    _name = 'chart.provisioning.task'
    _description = 'Tarea de preparación de servicio'
    _order = 'sequence, id'

    name = fields.Char(string='Tarea', required=True, translate=True)
    code = fields.Selection(
        selection=[
            ('data_verification', 'Verificación de identidad y datos'),
            ('access_setup', 'Accesos e integraciones'),
            ('data_import', 'Importación de datos'),
            ('user_setup', 'Configuración de usuario'),
            ('onboarding', 'Onboarding y capacitación'),
            ('other', 'Otra'),
        ],
        string='Tipo de paso', required=True, default='other')
    contract_id = fields.Many2one('chart.service.contract', string='Contrato',
                                  required=True, ondelete='cascade', index=True)
    job_id = fields.Many2one(
        'chart.provisioning.job', string='Trabajo de aprovisionamiento',
        ondelete='set null', index=True,
        help='Trabajo de aprovisionamiento asociado (si existe).')
    partner_id = fields.Many2one(related='contract_id.partner_id', store=True, index=True)
    company_id = fields.Many2one(related='contract_id.company_id', store=True)
    sequence = fields.Integer(default=10)
    state = fields.Selection(
        selection=[
            ('pending', 'Pendiente'),
            ('in_progress', 'En curso'),
            ('done', 'Hecha (verificada)'),
            ('blocked', 'Bloqueada'),
            ('skipped', 'No aplica'),
        ],
        string='Estado', default='pending', required=True)
    responsible_id = fields.Many2one('res.users', string='Responsable',
                                     default=lambda self: self.env.user)
    started_date = fields.Datetime(readonly=True, copy=False)
    done_date = fields.Datetime(readonly=True, copy=False)
    done_by_id = fields.Many2one('res.users', string='Verificada por', readonly=True, copy=False)
    completion_note = fields.Char(
        string='Evidencia / nota de cierre',
        help='Obligatoria para poder marcar la tarea como hecha. Se captura ANTES '
             'del cierre manual; queda guardada como evidencia de quién y cuándo.')
    notes = fields.Text(string='Notas')

    # ------------------------------------------------------------- transición
    def action_start(self):
        for task in self:
            if task.state not in ('pending', 'blocked'):
                raise UserError(_("Solo una tarea pendiente o bloqueada puede iniciarse."))
            task.write({'state': 'in_progress', 'started_date': fields.Datetime.now()})
        return True

    def action_block(self):
        for task in self:
            task.write({'state': 'blocked'})
        return True

    def action_done(self):
        """Cierre MANUAL con evidencia. Nunca se llama desde un cron ni desde la
        activación del contrato: el contrato no puede activarse con tareas abiertas."""
        for task in self:
            if task.state == 'done':
                continue
            if not task.completion_note:
                raise UserError(_(
                    "La tarea '%s' necesita una evidencia o nota de cierre antes "
                    "de marcarse como hecha.", task.name))
            task.write({
                'state': 'done',
                'done_date': fields.Datetime.now(),
                'done_by_id': self.env.uid,
            })
        return True

    def action_skip(self):
        for task in self:
            task.write({'state': 'skipped',
                        'completion_note': task.completion_note or _('No aplica')})
        return True

    def action_reset_pending(self):
        for task in self:
            task.write({'state': 'pending', 'started_date': False, 'done_date': False,
                        'done_by_id': False, 'completion_note': False})
        return True

    # ------------------------------------------------------------------ guard
    @api.constrains('state', 'done_date', 'done_by_id', 'completion_note')
    def _check_done_evidence(self):
        for task in self:
            if task.state == 'done' and not (task.done_date and task.done_by_id
                                             and task.completion_note):
                raise UserError(_(
                    "La tarea '%s' no puede quedar 'Hecha' sin fecha, verificador "
                    "y evidencia.", task.name))
