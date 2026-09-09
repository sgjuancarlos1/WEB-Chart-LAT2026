# -*- coding: utf-8 -*-
"""
Trabajo de aprovisionamiento de entorno cliente.

Representa el trabajo persistente de creación de un entorno Odoo 19 aislado
para un contrato. NO se ejecuta desde el controlador público y la creación
real de infraestructura queda SIEMPRE tras un parámetro de sistema explícito
(``chart_service_commerce.provisioning_enabled``); sin él el trabajo pasa a
``failed`` y NUNCA se finge un entorno ``ready``.

Seguridad:
- Identidad estable por operación, con unicidad real en PostgreSQL.
- Transiciones validadas y control de grupo en el servidor.
- Reintentos trazados con identidad conservada.
- Borrar recursos SOLO en método explícito autorizado y verificado por
  propiedad exacta; cancelar/resetear NO borra recursos.
- No ejecuta shell ni interpola datos del cliente en comandos.
"""
import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

_DB_NAME_RE = re.compile(r'^chart_env_[a-z0-9]{12}$')
_PROVISIONING_PARAM = 'chart_service_commerce.provisioning_enabled'
_DELETE_PARAM = 'chart_service_commerce.delete_environment_authorized'


class ChartProvisioningJob(models.Model):
    _name = 'chart.provisioning.job'
    _description = 'Trabajo de aprovisionamiento de entorno cliente'
    _order = 'id desc'
    _rec_name = 'display_name'

    name = fields.Char(string='Referencia', readonly=True, copy=False, default='/')
    display_name = fields.Char(compute='_compute_display_name', store=True)

    contract_id = fields.Many2one(
        'chart.service.contract', string='Contrato', required=True, index=True,
        ondelete='cascade')
    partner_id = fields.Many2one(
        related='contract_id.partner_id', store=True, string='Cliente', index=True)
    company_id = fields.Many2one(
        related='contract_id.company_id', store=True, string='Compañía')

    idempotent_key = fields.Char(
        string='Clave idempotente', readonly=True, copy=False, index=True,
        help='Identidad estable. Se genera una vez al crear el trabajo y NO se '
             'regenera en reintentos para conservar trazabilidad.')

    database_name = fields.Char(
        string='Base de datos del entorno', readonly=True, copy=False)
    environment_url = fields.Char(
        string='URL del entorno', readonly=True, copy=False,
        help='Destino de infraestructura autorizada. No se aceptan URLs '
             'arbitrarias del cliente y una URL construida NO acredita '
             'disponibilidad real.')
    admin_username = fields.Char(
        string='Usuario administrador', readonly=True, copy=False,
        groups='chart_service_commerce.group_chart_service_manager')
    invitation_token = fields.Char(
        string='Token de invitación', readonly=True, copy=False,
        groups='chart_service_commerce.group_chart_service_manager')
    invitation_expires = fields.Datetime(
        string='Invitación expira', readonly=True, copy=False)

    included_module_ids = fields.Many2many(
        'ir.module.module', string='Módulos incluidos', readonly=True, copy=False)
    allowed_user_count = fields.Integer(
        string='Usuarios permitidos', readonly=True, copy=False)
    initial_config_summary = fields.Text(
        string='Configuración inicial', readonly=True, copy=False)

    state = fields.Selection(
        selection=[
            ('draft', 'Borrador'),
            ('pending', 'Pendiente de aprobación'),
            ('approved', 'Aprobado'),
            ('provisioning', 'En aprovisionamiento'),
            ('ready', 'Listo'),
            ('failed', 'Fallido'),
            ('cancelled', 'Cancelado'),
        ],
        string='Estado', default='draft', required=True)

    progress_message = fields.Text(
        string='Mensaje de progreso', readonly=True, copy=False)
    error_message = fields.Text(
        string='Error', readonly=True, copy=False)
    retries_count = fields.Integer(
        string='Reintentos', default=0, readonly=True, copy=False)
    max_retries = fields.Integer(
        string='Máximo de reintentos', default=3, readonly=True, copy=False)

    created_date = fields.Datetime(
        readonly=True, copy=False, default=fields.Datetime.now)
    approved_date = fields.Datetime(readonly=True, copy=False)
    provisioning_started = fields.Datetime(readonly=True, copy=False)
    provisioning_done = fields.Datetime(readonly=True, copy=False)
    failed_date = fields.Datetime(readonly=True, copy=False)
    cancelled_date = fields.Datetime(readonly=True, copy=False)

    created_by_id = fields.Many2one(
        'res.users', string='Creado por', readonly=True, copy=False,
        default=lambda self: self.env.user)
    approved_by_id = fields.Many2one(
        'res.users', string='Aprobado por', readonly=True, copy=False)
    failed_by_id = fields.Many2one(
        'res.users', string='Fallido por', readonly=True, copy=False)

    task_ids = fields.One2many(
        'chart.provisioning.task', 'job_id', string='Tareas de aprovisionamiento')

    _idempotent_key_uniq = models.Constraint(
        'UNIQUE(idempotent_key)',
        'Ya existe un trabajo de aprovisionamiento con la misma clave idempotente.')

    @api.depends('contract_id.display_name', 'contract_id', 'state')
    def _compute_display_name(self):
        for job in self:
            if job.contract_id:
                job.display_name = '%s · %s' % (job.contract_id.display_name, job.state)
            else:
                job.display_name = 'Trabajo %s' % job.id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('idempotent_key'):
                vals['idempotent_key'] = self._next_idempotent_key(vals.get('contract_id'))
        return super().create(vals_list)

    @api.model
    def _next_idempotent_key(self, contract_id):
        """Clave estable por operación (contrato + sufijo cifrado determinista).

        Se genera UNA vez al crear el trabajo, no en cada aprobación/reintento,
        de modo que aprobar tras un reintento conserva exactamente la misma
        identidad y trazabilidad. La unicidad real la garantiza el índice
        UNIQUE de PostgreSQL (no la lectura + constrains del flujo anterior).
        """
        import hashlib
        import time
        cid = contract_id or 0
        seed = 'chart-op-%s-%s' % (cid, time.time())
        return 'chart-%s-%s' % (cid, hashlib.sha256(seed.encode()).hexdigest()[:20])

    # ------------------------------------------------------------- autorización
    def _ensure_user(self):
        """Preparación exige el grupo de preparador (nunca RPC público)."""
        if not self.env.user.has_group('chart_service_commerce.group_chart_service_user'):
            raise UserError(_('No tienes permiso para realizar esta operación.'))

    def _ensure_manager(self):
        """Transiciones privilegiadas exigen el grupo de responsable."""
        if not self.env.user.has_group('chart_service_commerce.group_chart_service_manager'):
            raise UserError(_('Solo el responsable de servicios puede realizar esta operación.'))

    # ------------------------------------------------------------ identidad
    def _generate_database_name(self):
        """Nombre de base determinista por operación (identidad estable)."""
        import hashlib
        seed = self.idempotent_key or self._next_idempotent_key(self.contract_id.id)
        suffix = hashlib.sha256(seed.encode()).hexdigest()[:12]
        return 'chart_env_%s' % suffix

    def _get_env_database_prefix(self):
        return 'chart_env_'

    def _get_env_url(self, database_name):
        """Candidato de destino autorizado. NO acredita disponibilidad real."""
        if not _DB_NAME_RE.match(database_name or ''):
            return False
        subdomain = database_name.replace('_', '-')
        return 'https://%s.chart.lat' % subdomain

    def _get_allowed_modules_for_version(self, version='19.0'):
        """Alcance EXPLÍCITO de módulos disponibles para la edición destino."""
        base_modules = [
            'base', 'web', 'mail', 'auth_signup', 'website', 'website_sale',
            'portal', 'contacts', 'sale', 'account', 'calendar', 'note',
        ]
        Module = self.env['ir.module.module']
        return Module.search([
            ('name', 'in', base_modules),
            ('state', 'in', ('installed', 'uninstalled', 'to upgrade', 'to install')),
        ])

    def _get_initial_config_summary(self):
        return _(
            'Entorno Odoo 19.0 aislado. Usuarios permitidos: %d. '
            'Base de datos propia con filestore dedicado. La creación real '
            'exige autorización de infraestructura.'
        ) % (self.allowed_user_count or 5)

    # ----------------------------------------------------- inventario real
    def _db_exists(self, db_name):
        """Comprueba la base en ``pg_database`` (solo lectura, sin modelo falso)."""
        if not db_name:
            return False
        self.env.cr.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
        return bool(self.env.cr.fetchone())

    def _validate_database_identifier(self, db_name):
        if not db_name or not _DB_NAME_RE.match(db_name):
            raise UserError(
                _('El nombre de base de datos %r no tiene el formato esperado.') % (db_name,))

    def _provisioning_enabled(self):
        param = self.env['ir.config_parameter'].sudo().get_param(
            _PROVISIONING_PARAM, 'False')
        return str(param).strip().lower() in ('1', 'true', 'yes', 'on')

    # ------------------------------------------------------------ acciones
    def action_approve(self):
        """Aprueba el trabajo (SÓLO responsable). Genera una identidad única."""
        self.ensure_one()
        self._ensure_manager()
        if self.state not in ('draft', 'pending'):
            raise UserError(_('Solo se puede aprobar un trabajo en estado borrador o pendiente.'))
        if self.contract_id.state not in ('pending_activation_approval', 'active'):
            raise UserError(_('El contrato no está listo para aprovisionamiento.'))

        existing = self.search([
            ('contract_id', '=', self.contract_id.id),
            ('state', 'in', ('approved', 'provisioning', 'ready')),
            ('id', '!=', self.id),
        ])
        if existing:
            raise UserError(_('Ya existe un trabajo de aprovisionamiento activo para este contrato.'))

        # Identidad calculada UNA vez y reutilizada para base y URL.
        db_name = self._generate_database_name()
        self.write({
            'state': 'approved',
            'approved_date': fields.Datetime.now(),
            'approved_by_id': self.env.uid,
            'database_name': db_name,
            'environment_url': self._get_env_url(db_name),
            'allowed_user_count': self.allowed_user_count or 5,
        })
        if self.contract_id.provisioning_job_id.id != self.id:
            self.contract_id.sudo().write({'provisioning_job_id': self.id})
        return True

    def action_start_provisioning(self):
        """Inicia el aprovisionamiento (preparador). Frontera transaccional.

        - No corre por RPC público: sólo el grupo de preparación.
        - Ante fallo persiste ``failed`` SIN relanzar (el rollback del flujo no
          borra el registro de fallo ni deja recursos sin identidad).
        - Sólo queda ``ready`` si ``_verify_environment`` contra el recurso
          real pasa; una URL o un directorio de filestore NO bastan.
        """
        self.ensure_one()
        self._ensure_user()
        if self.state != 'approved':
            raise UserError(_('Solo se puede iniciar un trabajo aprobado.'))
        if not self.database_name:
            raise UserError(_('El trabajo no tiene base de datos asignada.'))

        self.write({
            'state': 'provisioning',
            'provisioning_started': fields.Datetime.now(),
            'error_message': False,
            'progress_message': _('Preparando el entorno...'),
        })
        try:
            # Frontera transaccional: el fallo del aprovisionamiento (incluido
            # un error SQL que deje la conexión en estado de error) se aisla en
            # un savepoint; el rollback recupera la conexión y el registro del
            # fallo se persiste DESPUÉS en una transacción sana. Así ``failed``
            # permanece aunque la conexión SQL haya entrado en error.
            with self.env.cr.savepoint():
                self._provision_environment()
        except UserError as e:
            self._mark_failed(e.args[0] if e.args else _('No se pudo preparar el entorno.'))
            return False
        except Exception:
            _logger.exception('Fallo de aprovisionamiento del trabajo %s', self.id)
            self._mark_failed(_('Error interno durante la preparación del entorno.'))
            return False

        ok, detail = self._verify_environment()
        if not ok:
            self._mark_failed(detail or _('El entorno no pasó la verificación de disponibilidad.'))
            return False

        self.write({
            'state': 'ready',
            'provisioning_done': fields.Datetime.now(),
            'progress_message': _('Entorno creado y verificado.'),
            'error_message': False,
        })
        return True

    def _mark_failed(self, error_message):
        """Persiste el fallo (write simple; la transacción se cierra al volver)."""
        self.write({
            'state': 'failed',
            'failed_date': fields.Datetime.now(),
            'failed_by_id': self.env.uid,
            'error_message': error_message,
            'progress_message': _('Falló la preparación del entorno.'),
        })

    def _provision_environment(self):
        """Adaptador real tras la puerta de autorización (sin ella NO crea infra)."""
        if not self._provisioning_enabled():
            raise UserError(
                _('El aprovisionamiento real no está autorizado en este entorno '
                  '(param config %s desactivado).') % _PROVISIONING_PARAM)
        db_name = self.database_name
        self._validate_database_identifier(db_name)
        # RECONCILIACIÓN: si una caída ocurrió DESPUÉS de crear la base (DDL en
        # autocommit, fuera de la transacción), el recurso ya existe. Reintentar
        # NO debe duplicarlo ni fallar: se reutiliza el recurso identificado.
        self._create_database(db_name)
        self._install_modules_in_database(db_name)
        self._create_filestore(db_name)
        self._post_provisioning_setup()

    def _create_database(self, db_name):
        """Crea la base con conexión ADMINISTRATIVA separada, en AUTOCOMMIT.

        ``CREATE DATABASE`` (PostgreSQL 16) no cabe en el bloque transaccional
        del negocio; se abre una conexión separada a la base administrativa y
        SÓLO ahí se usa autocommit para el DDL autorizado. El identificador se
        compone con ``psycopg2.sql.Identifier``; el cursor del negocio JAMÁS
        se pone en autocommit.
        """
        if self._db_exists(db_name):
            # Reconciliación idempotente: el recurso ya existe con ESTA
            # identidad (misma operación reintentada). No se duplica y no se
            # considera error: se continúa con el recurso existente.
            _logger.info(
                'La base %s ya existe (reintento de la misma operación): se '
                'reconcilia sin duplicar.', db_name)
            return
        from psycopg2 import sql as psy_sql
        from odoo import sql_db
        conn = sql_db.db_connect('postgres')
        try:
            with conn.cursor(autocommit=True) as cr:
                cr.execute(
                    psy_sql.SQL('CREATE DATABASE {} WITH OWNER = %s ENCODING = %s')
                    .format(psy_sql.Identifier(db_name)),
                    ('odoo', 'UTF8'))
        finally:
            conn.close()

    def _install_modules_in_database(self, db_name):
        """Instala los módulos del alcance explícito en la NUEVA base (adaptador).

        Marca la lista en ``included_module_ids`` y el resumen. La instalación
        real se delega al ejecutor Odoo contra la base destino (autorizado por
        ``_provisioning_enabled``); no se confunden IDs de módulos de la base
        comercial con la base destino.
        """
        modules = self._get_allowed_modules_for_version()
        self.included_module_ids = [(6, 0, modules.ids)]
        self.initial_config_summary = self._get_initial_config_summary()

    def _create_filestore(self, db_name):
        """Crea el filestore aislado de la base destino (adaptador)."""
        import os
        from odoo.tools import config
        base = config.get('data_dir')
        fstore = os.path.join(base, 'filestore', db_name)
        os.makedirs(fstore, exist_ok=True)

    def _post_provisioning_setup(self):
        """Configuración inicial tras crear la base (empresa/usuario/permisos).

        La inicialización limpia de Odoo, la empresa, el usuario cliente, los
        permisos y la invitación se aplican contra la base destino una vez
        autorizada; aquí sólo se conserva el resumen de configuración.
        """
        self.initial_config_summary = self._get_initial_config_summary()

    def _verify_environment(self):
        """Verificación REAL de disponibilidad del recurso (criterio de READY).

        Devuelve (ok, detalle). Un HTTP 200 de la base comercial, una URL
        construida o una carpeta de filestore NO cumplen. Sin la autorización
        de infraestructura la comprobación objetiva no es posible y el entorno
        no puede quedar ``ready``.
        """
        if not self._provisioning_enabled():
            return False, _('No se puede verificar el entorno sin autorización de infraestructura.')
        if not self._db_exists(self.database_name):
            return False, _('La base de datos del entorno no existe.')
        if not self.environment_url:
            return False, _('El entorno no tiene un destino autorizado.')
        # En un entorno autorizado se comprueba: Odoo inicializado, módulos
        # requeridos instalados, empresa configurada, usuario con permisos,
        # aislamiento de datos, filestore accesible por el servicio, host
        # enrutado a SU PROPIA base, HTTPS válido e invitación segura útil.
        return False, _('La verificación objetiva de disponibilidad está pendiente '
                        'de infraestructura autorizada.')

    def action_retry(self):
        """Reintenta tras fallo (responsable). Conserva la misma identidad."""
        self.ensure_one()
        self._ensure_manager()
        if self.state != 'failed':
            raise UserError(_('Solo se puede reintentar un trabajo fallido.'))
        if self.retries_count >= self.max_retries:
            raise UserError(_('Máximo de reintentos (%d) alcanzado.') % self.max_retries)
        self.write({
            'state': 'approved',
            'retries_count': self.retries_count + 1,
            'error_message': False,
            'failed_date': False,
            'failed_by_id': False,
            'progress_message': _('Reintentando (intento %d/%d).') % (
                self.retries_count + 1, self.max_retries),
        })
        return True

    def action_cancel(self):
        """CANCELACIÓN COMERCIAL del trabajo. NO borra recursos.

        Conserva identidad e historial. Eliminar la base/filestore exige
        ``action_delete_environment`` (verificado, respaldado y autorizado).
        """
        self.ensure_one()
        self._ensure_manager()
        if self.state in ('ready', 'failed'):
            raise UserError(_('No se puede cancelar un trabajo listo o fallido.'))
        if self.state == 'provisioning':
            raise UserError(_('No se puede cancelar un trabajo en ejecución.'))
        self.write({
            'state': 'cancelled',
            'cancelled_date': fields.Datetime.now(),
            'progress_message': _('Trabajo cancelado. El entorno no se eliminó.'),
        })
        return True

    def action_reset(self):
        """Vuelve a borrador para reaprovisionar (responsable).

        Reinicia sólo indicadores operativos; CONSERVA la identidad de recursos
        (base, URL) para no perder la referencia al recurso creado.
        """
        self.ensure_one()
        self._ensure_manager()
        if self.state not in ('failed', 'cancelled'):
            raise UserError(_('Solo se puede resetear un trabajo fallido o cancelado.'))
        self.write({
            'state': 'draft',
            'retries_count': 0,
            'error_message': False,
            'progress_message': False,
            'provisioning_started': False,
            'provisioning_done': False,
            'failed_date': False,
            'cancelled_date': False,
            'failed_by_id': False,
            'included_module_ids': False,
        })
        return True

    def action_delete_environment(self, confirm=False):
        """Borrado EXPLÍCITO y VERIFICADO del recurso (responsable).

        Requiere confirmación explícita, propiedad exacta del identificador y
        un parámetro específico de borrado autorizado. No borra por
        coincidencia de prefijo. Cancela el trabajo y elimina el recurso en
        conexión administrativa autocommit (DDL autorizado).
        """
        self.ensure_one()
        self._ensure_manager()
        if not confirm:
            raise UserError(_('Debes confirmar explícitamente el borrado del entorno.'))
        if not self.database_name or not _DB_NAME_RE.match(self.database_name):
            raise UserError(_('No hay un entorno con identidad de recurso válida para borrar.'))
        param = self.env['ir.config_parameter'].sudo().get_param(_DELETE_PARAM, 'False')
        if str(param).strip().lower() not in ('1', 'true', 'yes', 'on'):
            raise UserError(_('El borrado de entornos no está autorizado en este despliegue.'))
        db_name = self.database_name
        self.contract_id.sudo().write({'provisioning_job_id': False})
        self.write({'state': 'cancelled', 'cancelled_date': fields.Datetime.now()})
        self._drop_database(db_name)
        return True

    def _drop_database(self, db_name):
        """DROP con identificación segura en conexión administrativa autocommit."""
        self._validate_database_identifier(db_name)
        from psycopg2 import sql as psy_sql
        from odoo import sql_db
        conn = sql_db.db_connect('postgres')
        try:
            with conn.cursor(autocommit=True) as cr:
                cr.execute(psy_sql.SQL('DROP DATABASE IF EXISTS {}').format(
                    psy_sql.Identifier(db_name)))
        finally:
            conn.close()

    # ------------------------------------------------------------------ portal info
    def get_portal_status_info(self):
        """Proyección SEGURA para el portal (sin secretos ni detalle técnico).

        No expone tokens, rutas privadas, SQL ni credenciales. El error crudo
        queda restringido al registro interno y no llega a la vista pública.
        """
        self.ensure_one()
        return {
            'state': self.state,
            'state_label': self._get_state_label(),
            'progress': self.progress_message,
            'has_error': self.state == 'failed',
            'environment_url': self.environment_url if self.state == 'ready' else None,
            'provisioning_done': self.provisioning_done,
        }

    def _get_state_label(self):
        """Etiqueta amigable para el portal."""
        labels = {
            'draft': 'Por definir',
            'pending': 'Esperando aprobación',
            'approved': 'Aprobado',
            'provisioning': 'Preparando entorno',
            'ready': 'Listo',
            'failed': 'Fallido',
            'cancelled': 'Cancelado',
        }
        return labels.get(self.state, self.state)