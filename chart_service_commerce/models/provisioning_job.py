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
import json
import logging
import os
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

_DB_NAME_RE = re.compile(r'^chart_env_[a-z0-9]{12}$')
_PROVISIONING_PARAM = 'chart_service_commerce.provisioning_enabled'
_DELETE_PARAM = 'chart_service_commerce.delete_environment_authorized'

# Estados del contrato en los que la preparación TÉCNICA puede comenzar (la
# comprobación/activación del entorno NO es prerrequisito del job).
_JOB_APPROVABLE_CONTRACT_STATES = (
    'pending_preparation',
    'in_preparation',
    'pending_environment_check',
    'pending_activation_approval',
    'active',
)

# Identificador PostgreSQL válido para el rol propietario de la base destino.
_DB_OWNER_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_$]{0,62}$')

# Ejecutor Odoo del ENTORNO (binario/config/data_dir dedicados). Sin esta
# configuración no se instala, configura ni verifica nada sobre la base
# destino: el job falla con un error accionable (nunca finge éxito).
_EXEC_BIN_PARAM = 'chart_service_commerce.environment_executor_bin'
_EXEC_CONFIG_PARAM = 'chart_service_commerce.environment_executor_config'
_EXEC_DATA_DIR_PARAM = 'chart_service_commerce.environment_executor_data_dir'
_EXEC_INIT_TIMEOUT = 3600
_EXEC_SHELL_TIMEOUT = 600


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
        # DEPENDENCIA CIRCULAR RESUELTA: la preparación TÉCNICA (job) puede
        # comenzar antes de comprobar/activar el entorno. Sólo se exige que la
        # contratación esté confirmada y no cancelada/en pausa/borrador: el
        # entorno se comprueba y el contrato se activa DESPUÉS por la política
        # comercial y el responsable, nunca por el job.
        if self.contract_id.state not in _JOB_APPROVABLE_CONTRACT_STATES:
            raise UserError(_(
                'El contrato está en %s. La preparación técnica solo comienza '
                'tras la contratación confirmada y antes o durante la activación '
                'comercial (nunca en borrador, pausa o cancelación).',
                dict(self.contract_id._fields['state'].selection)[self.contract_id.state]))

        # Exclusión EFECTIVA entre DOS JOBS del mismo contrato (y no solo por el
        # índice único de idempotent_key): la aprobación se serializa en la fila
        # del contrato. Dos ejecutores concurrentes no pueden leer "no existe
        # otro job activo" a la vez: el segundo espera al candado y relee el
        # resultado del primero en la misma transacción.
        self.env.cr.execute(
            'SELECT id FROM "chart_service_contract" WHERE id = %s FOR UPDATE',
            (self.contract_id.id,))
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
        if not self.database_name:
            raise UserError(_('El trabajo no tiene base de datos asignada.'))

        # Exclusión EFECTIVA entre DOS EJECUTORES del mismo job: la ejecución
        # se serializa en la fila del trabajo. El segundo ejecutor espera aquí
        # y, al adquirir el candado, relee el estado REAL (READ COMMITTED) en
        # lugar de confiar en la caché previa a la espera.
        self.env.cr.execute(
            'SELECT id FROM "chart_provisioning_job" WHERE id = %s FOR UPDATE',
            (self.id,))
        self.invalidate_recordset(
            ['state', 'error_message', 'progress_message', 'retries_count'])
        if self.state != 'approved':
            raise UserError(_(
                'Otro ejecutor ya está preparando o completó este trabajo '
                '(estado: %s).', self.state))

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

        # La verificación también se protege con savepoint: un fallo o timeout
        # en _verify_environment (p. ej. red/SQL) NO aborta la transacción sin
        # persistir: se recupera la conexión y se registra ``failed`` igual que
        # cualquier otra fase del aprovisionamiento.
        try:
            with self.env.cr.savepoint():
                ok, detail = self._verify_environment()
        except UserError as e:
            self._mark_failed(e.args[0] if e.args else _('No se pudo verificar el entorno.'))
            return False
        except Exception:
            _logger.exception('Fallo de verificación del entorno del trabajo %s', self.id)
            self._mark_failed(_('Error interno durante la verificación del entorno.'))
            return False
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
        # NO debe duplicarlo ni fallar: se reutiliza el recurso identificado
        # (solo si el marcador de propiedad demuestra que es de esta operación).
        self._create_database(db_name)
        # El filestore se asegura ANTES de la inicialización para que el
        # ejecutor escriba en su data_dir dedicado desde el primer arranque.
        self._create_filestore(db_name)
        self._install_modules_in_database(db_name)
        self._post_provisioning_setup()

    def _create_database(self, db_name):
        """Crea la base con conexión ADMINISTRATIVA separada, en AUTOCOMMIT.

        ``CREATE DATABASE`` (PostgreSQL 16) no cabe en el bloque transaccional
        del negocio; se abre una conexión separada a la base administrativa y
        SÓLO ahí se usa autocommit para el DDL autorizado.

        Correcciones frente a la revisión:
        - El propietario (OWNER) también es un IDENTIFICADOR y se compone con
          ``psycopg2.sql.Identifier`` (nunca como parámetro literal %s) a
          partir de un rol explícitamente configurado; sin rol no se adivina.
        - Una conexión separada al mismo PostgreSQL NO representa un ejecutor
          con privilegios separados: la creación usa las credenciales de la
          configuración actual y falla si no tiene el privilegio real.
        - Una coincidencia de NOMBRE no acredita propiedad: tras crear, se
          estampa un marcador de propiedad (COMMENT ON DATABASE) con la
          identidad del trabajo. Reutilizar una base existente exige que el
          marcador coincida; si no, se rechaza (no se adopta un recurso ajeno).
        """
        if self._db_exists(db_name):
            # Reconciliación SOLO si la propiedad es demostrable. Una base con
            # el mismo nombre pero sin nuestro marcador (o con otro) no se
            # adopta: la identidad se registró ANTES del efecto externo y el
            # reintento conserva exactamente la misma operación.
            self._ensure_database_ownership(db_name)
            _logger.info(
                'La base %s ya existe y su marcador de propiedad coincide: se '
                'reconcilia sin duplicar.', db_name)
            return
        from psycopg2 import sql as psy_sql
        from odoo import sql_db
        owner = self._environment_db_owner()
        if not owner:
            raise UserError(_(
                'No hay un rol propietario configurado para la base destino '
                '(param chart_service_commerce.environment_db_owner). No se '
                'adivina el propietario.'))
        conn = sql_db.db_connect('postgres')
        try:
            with conn.cursor(autocommit=True) as cr:
                cr.execute(
                    psy_sql.SQL('CREATE DATABASE {} WITH OWNER = {} ENCODING = {}')
                    .format(
                        psy_sql.Identifier(db_name),
                        psy_sql.Identifier(owner),
                        psy_sql.SQL("'UTF8'")))
                # Estampa de propiedad tras el efecto externo: la identidad del
                # trabajo ya quedó registrada en el job antes de crear.
                cr.execute(
                    psy_sql.SQL('COMMENT ON DATABASE {} IS {}').format(
                        psy_sql.Identifier(db_name),
                        psy_sql.Literal(self._database_marker())))
        finally:
            conn.close()

    def _environment_db_owner(self):
        """Rol propietario de la base destino (config explícita, sin hardcode)."""
        param = self.env['ir.config_parameter'].sudo().get_param(
            'chart_service_commerce.environment_db_owner', '')
        owner = str(param or '').strip()
        if owner and not _DB_OWNER_RE.match(owner):
            raise UserError(_(
                '%r no es un identificador de rol PostgreSQL válido.', owner))
        return owner

    def _database_marker(self):
        """Marcador de propiedad de la operación (identidad, sin secretos)."""
        return 'chart_provisioning:%s' % (self.idempotent_key or '')

    def _read_database_comment(self, db_name):
        """Comentario (marcador) de la base, lectura directa de pg_catalog."""
        self._validate_database_identifier(db_name)
        self.env.cr.execute(
            "SELECT pg_catalog.shobj_description(d.oid, 'pg_database') "
            'FROM pg_catalog.pg_database d WHERE datname = %s', (db_name,))
        row = self.env.cr.fetchone()
        return (row[0] or '') if row else ''

    def _ensure_database_ownership(self, db_name):
        """Verifica la propiedad ANTES de adoptar un recurso ya existente.

        El marcador debe coincidir EXACTAMENTE con la identidad de esta
        operación. Un nombre coincidente sin marcador, o con el marcador de
        otra operación, se rechaza: la reconciliación nunca adopta recursos
        ajenos ni borra la evidencia de una operación distinta.
        """
        if not self._db_exists(db_name):
            return False
        expected = self._database_marker()
        comment = self._read_database_comment(db_name)
        if not comment:
            raise UserError(_(
                'La base %s ya existe pero NO tiene marcador de propiedad de '
                'aprovisionamiento. No se adopta un recurso ajeno por '
                'coincidencia de nombre.', db_name))
        if comment != expected:
            raise UserError(_(
                'La base %s ya existe y su marcador (%s) no corresponde a esta '
                'operación (%s). Revisa manualmente la propiedad del recurso.',
                db_name, comment[:80], expected))
        return True

    # ------------------------------------------------------------------ ejecutor
    def _executor_config(self):
        """Configuración del ejecutor Odoo del ENTORNO (binario/config/data_dir).

        El ejecutor es el proceso Odoo aislado que inicializa la base destino
        con su propia configuración (dbfilter propio, filestore dedicado). Sin
        binario/config el job falla con un error accionable: ninguna operación
        se simula ni se da por hecha sin el ejecutor real.
        """
        ICP = self.env['ir.config_parameter'].sudo()
        return {
            'bin': str(ICP.get_param(_EXEC_BIN_PARAM, '') or '').strip(),
            'config': str(ICP.get_param(_EXEC_CONFIG_PARAM, '') or '').strip(),
            'data_dir': str(ICP.get_param(_EXEC_DATA_DIR_PARAM, '') or '').strip(),
        }

    def _require_executor(self, reason):
        cfg = self._executor_config()
        if not cfg['bin'] or not cfg['config']:
            raise UserError(_(
                '%s No hay ejecutor Odoo del entorno configurado (parámetros '
                '%s y %s). Sin esa configuración dedicada no se realiza ninguna '
                'operación sobre la base destino y el trabajo no continúa.',
                reason, _EXEC_BIN_PARAM, _EXEC_CONFIG_PARAM))
        return cfg

    def _run_env_odoo(self, db_name, extra_args, timeout=None, stdin_text=None,
                      env_extra=None):
        """Ejecuta el binario Odoo del ENTORNO contra la base destino.

        - Argumentos como LISTA (sin shell): los datos del cliente jamás se
          interpolan en un comando.
        - La base debe cumplir el patrón aislado ``chart_env_``.
        - Devuelve (returncode, salida combinada acotada para el registro).
        """
        import subprocess
        self._validate_database_identifier(db_name)
        cfg = self._require_executor('Ejecución del entorno %s.' % db_name)
        cmd = [cfg['bin'], '--config', cfg['config'], '-d', db_name,
               '--no-http', '--stop-after-init'] + list(extra_args)
        _env = dict(os.environ)
        if env_extra:
            _env.update(env_extra)
        proc = subprocess.run(
            cmd, input=stdin_text, capture_output=True, text=True, env=_env,
            timeout=timeout or _EXEC_INIT_TIMEOUT)
        out = ((proc.stdout or '') + (proc.stderr or ''))[-4000:]
        return proc.returncode, out

    def _install_modules_in_database(self, db_name):
        """Instala en la NUEVA base los módulos del alcance explícito.

        Conserva la lista/resumen como trazabilidad y DELEGA la instalación
        real al ejecutor Odoo del entorno (la base ya existe y es propiedad
        verificada de esta operación). Un returncode distinto de 0 aborta: el
        entorno no queda listo si la instalación falló o no se ejecutó.
        """
        modules = self._get_allowed_modules_for_version()
        names = sorted({m.name for m in modules
                        if m.name and re.fullmatch(r'[a-z0-9_]+', m.name)})
        if not names:
            raise UserError(_('No hay módulos del alcance explícito para instalar.'))
        self.included_module_ids = [(6, 0, modules.ids)]
        self.initial_config_summary = self._get_initial_config_summary()
        self._require_executor('Instalación de módulos en %s.' % db_name)
        rc, out = self._run_env_odoo(
            db_name, ['-i', ','.join(names)], timeout=_EXEC_INIT_TIMEOUT)
        if rc != 0:
            raise UserError(_(
                'El ejecutor no pudo instalar los módulos en %s (código %s). '
                'El entorno NO queda disponible.', db_name, rc))
        _logger.info('Módulos instalados en %s por el ejecutor del entorno.', db_name)

    def _create_filestore(self, db_name):
        """Asegura el filestore de la base destino en el data_dir del EJECUTOR.

        El filestore del entorno pertenece al data_dir dedicado del ejecutor,
        NO al data_dir del proceso que corre el job. Sin data_dir de ejecutor
        configurado no se crea un filestore huérfano en el directorio del job.
        """
        self._validate_database_identifier(db_name)
        cfg = self._require_executor('Creación del filestore de %s.' % db_name)
        base = cfg['data_dir']
        if not base:
            raise UserError(_(
                'Sin data_dir de ejecutor (%s) no se crea el filestore del '
                'entorno.', _EXEC_DATA_DIR_PARAM))
        fstore = os.path.join(base, 'filestore', db_name)
        os.makedirs(fstore, exist_ok=True)

    def _post_provisioning_setup(self):
        """Configura empresa, usuario e invitación en la base destino (adaptador).

        Ejecuta el script de inicialización dedicado del módulo dentro del
        ejecutor (``odoo shell`` contra la base destino) con el payload por
        variable de entorno en JSON: nada de datos del cliente entra en un
        comando. Conserva en el job el resultado devuelto por el script
        (usuario administrador, token y expiración de la invitación). Si el
        script falla o no devuelve un resultado completo, el trabajo falla.
        """
        db_name = self.database_name
        self._validate_database_identifier(db_name)
        self._require_executor('Configuración inicial de %s.' % db_name)
        self.initial_config_summary = self._get_initial_config_summary()
        payload = self._environment_setup_payload()
        script = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            '..', 'data', 'environment_setup.py')
        if not os.path.isfile(script):
            raise UserError(_('No se encontró el script de inicialización del entorno.'))
        with open(script, encoding='utf-8') as fh:
            script_text = fh.read()
        rc, out = self._run_env_odoo(
            db_name, ['shell'], timeout=_EXEC_SHELL_TIMEOUT,
            stdin_text=script_text,
            env_extra={'CHART_ENV_SETUP_JSON': json.dumps(payload)})
        if rc != 0:
            raise UserError(_(
                'El ejecutor no pudo configurar %s (código %s). El entorno NO '
                'queda disponible.', db_name, rc))
        result = self._parse_env_result(out)
        if not result:
            raise UserError(_(
                'El ejecutor terminó sin devolver el resultado de la '
                'configuración de %s.', db_name))
        self.write({
            'admin_username': result.get('admin_username') or False,
            'invitation_token': result.get('invitation_token') or False,
            'invitation_expires': result.get('invitation_expires') or False,
        })

    def _environment_setup_payload(self):
        """Payload JSON del setup (sin secretos en metadata del job).

        Incluye ``environment_url`` porque el reporte post-setup del ejecutor
        debe acreditar el MISMO destino autorizado de este trabajo; sin ese
        dato el ejecutor no puede dejar el reporte que ``_verify_environment``
        exige (y la verificación nunca pasaría aunque todo lo demás esté bien).
        """
        partner = self.contract_id.partner_id
        company = partner.commercial_partner_id or partner
        return {
            'company_name': (company.name or _('Empresa Cliente')).strip()[:120],
            'company_email': (company.email or '').strip()[:120],
            'admin_login': (self.idempotent_key or 'admin').strip(),
            'admin_email': (partner.email or '').strip()[:120],
            'admin_name': (partner.name or _('Cliente')).strip()[:120],
            'admin_password': self._new_env_secret(),
            'allowed_users': max(int(self.allowed_user_count or 5), 1),
            'database_name': self.database_name,
            'environment_url': self.environment_url or False,
        }

    def _new_env_secret(self):
        """Contraseña temporal aleatoria para el administrador del entorno.

        Se entrega al responsable por el canal seguro existente; no se
        persiste en metadata del job ni en logs.
        """
        import secrets
        return secrets.token_urlsafe(24)

    def _parse_env_result(self, output):
        """Extrae la línea ``CHART_ENV_RESULT: {json}`` de la salida del script."""
        marker = 'CHART_ENV_RESULT:'
        for line in (output or '').splitlines():
            if marker in line:
                try:
                    return json.loads(line.split(marker, 1)[1].strip())
                except (ValueError, TypeError):
                    return {}
        return {}

    def _verify_environment(self):
        """Verificación REAL de disponibilidad del recurso (criterio de READY).

        Devuelve (ok, detalle). Un HTTP 200 de la base comercial, una URL
        construida, un filestore vacío o un 200 de error NO cumplen. Sin
        autorización de infraestructura la comprobación objetiva no es posible
        y el entorno nunca queda ``ready``.

        Comprobaciones (todas deben pasar en un despliegue autorizado):
        1. Parámetro de infraestructura habilitado.
        2. La base destino existe y su marcador de propiedad coincide con esta
           operación (coincidencia de nombre NO basta).
        3. Hay un destino (environment_url) en un dominio enrutado autorizado
           (lista de dominios explícita; sin lista no hay enrutamiento seguro).
        4. El ejecutor dejó un reporte de verificación post-setup con Odoo
           inicializado, módulos instalados, empresa configurada, usuario con
           permisos y filestore accesible.
        5. El reporte corresponde al destino exacto de este trabajo (no se
           reescribe el destino para hacer pasar la demostración).
        """
        if not self._provisioning_enabled():
            return False, _('No se puede verificar el entorno sin autorización de infraestructura.')
        db_name = self.database_name
        if not db_name or not _DB_NAME_RE.match(db_name or ''):
            return False, _('El trabajo no tiene una identidad de recurso válida.')
        if not self._db_exists(db_name):
            return False, _('La base de datos del entorno no existe.')
        try:
            self._ensure_database_ownership(db_name)
        except UserError as e:
            return False, (e.args[0] if e.args else _('El recurso no pertenece a esta operación.'))
        if not self.environment_url:
            return False, _('El entorno no tiene un destino autorizado.')
        allowed = self.env['ir.config_parameter'].sudo().get_param(
            'chart_service_commerce.allowed_environment_domains', '')
        if not allowed.strip():
            return False, _('No hay dominios de entorno autorizados configurados.')
        from urllib.parse import urlparse
        parsed = urlparse(self.environment_url)
        host = (parsed.hostname or '').lower()
        allowed_hosts = {h.strip().lower() for h in allowed.split(',') if h.strip()}
        if not any(host == h or host.endswith('.' + h) for h in allowed_hosts):
            return False, _('El destino %s no está en los dominios autorizados.', self.environment_url)
        report = self._environment_report(db_name)
        if not report:
            return False, _('El ejecutor no dejó el reporte de verificación post-setup.')
        required = ('odoo_initialized', 'modules_installed', 'company_configured',
                    'user_created', 'filestore_ready', 'invitation_ok')
        missing = [k for k in required if not report.get(k)]
        if missing:
            return False, _('El reporte de verificación no acredita: %s.',
                            ', '.join(missing))
        if report.get('environment_url') != self.environment_url:
            return False, _('El reporte verificado no corresponde al destino exacto de este trabajo.')
        return True, _('Entorno verificado: Odoo inicializado, módulos instalados, '
                       'empresa y usuario configurados y filestore accesible.')

    def _environment_report(self, db_name):
        """Reporte de verificación post-setup escrito por el EJECUTOR.

        Ruta: <data_dir del ejecutor>/chart_env_reports/<db_name>.json. Es un
        artefacto del ejecutor (no del job): su presencia acredita que la
        inicialización terminó correctamente en la base destino.
        """
        cfg = self._executor_config()
        if not cfg['data_dir']:
            return {}
        report_path = os.path.join(
            cfg['data_dir'], 'chart_env_reports', '%s.json' % db_name)
        try:
            with open(report_path, encoding='utf-8') as fh:
                return json.load(fh)
        except (IOError, OSError, ValueError):
            return {}

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