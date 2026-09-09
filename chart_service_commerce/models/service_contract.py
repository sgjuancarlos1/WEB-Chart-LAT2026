# -*- coding: utf-8 -*-
"""Contrato de servicio.

Reglas corregidas frente a la propuesta anterior:
- Se construye desde las LÍNEAS de un sale.order nativo: conserva variante
  (product.product), cantidad y precio congelado. Un Many2many de
  product.template no representa importes ni variantes.
- Un pedido puede originar VARIOS contratos (uno por servicio), cada uno con
  preparación y activación independientes.
- No se duplica el pedido ni se crea un segundo sale.order.
- Las 72 h son un objetivo informativo (activation_target_date), no un
  disparador: NINGÚN cron cambia el estado a 'active'.
- Sin claves API ni secretos en metadata.
"""
import logging
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Plazo objetivo de entrega (horas naturales) desde la contratación.
ACTIVATION_TARGET_HOURS = 72


def _groupby_lines(lines):
    """Agrupa sale.order.line por variante preservando el orden de aparición."""
    groups = {}
    order = []
    for line in lines:
        key = line.product_id.id
        if key not in groups:
            groups[key] = line.browse()
            order.append(key)
        groups[key] |= line
    return [(groups[k][:1].product_id, groups[k]) for k in order]


class ChartServiceContract(models.Model):
    _name = 'chart.service.contract'
    _description = 'Contrato de servicio'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'
    _rec_name = 'display_name'

    # ------------------------------------------------------------------ name
    name = fields.Char(string='Referencia', readonly=True, copy=False, default='/')
    display_name = fields.Char(compute='_compute_display_name', store=True)

    company_id = fields.Many2one(
        'res.company', string='Compañía', required=True,
        default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(
        'res.currency', string='Moneda', required=True,
        compute='_compute_currency_id', store=True, readonly=False)
    partner_id = fields.Many2one(
        'res.partner', string='Cliente', required=True, index=True,
        compute='_compute_from_order', store=True, readonly=False,
        tracking=True)
    authorized_partner_ids = fields.Many2many(
        'res.partner', string='Contactos autorizados',
        help='Otros contactos (p. ej. de la misma empresa) autorizados '
             'EXPLÍCITAMENTE a ver este contrato. Por defecto solo el titular '
             '(partner_id) tiene acceso; el acceso de otros miembros requiere '
             'autorización expresa aquí.')
    user_id = fields.Many2one(
        'res.users', string='Usuario portal', compute='_compute_user_id', store=True,
        readonly=True)

    @api.depends('partner_id.user_ids')
    def _compute_user_id(self):
        for contract in self:
            contract.user_id = contract.partner_id.user_ids[:1].id

    # ------------------------------------------------------------- origen
    sale_order_id = fields.Many2one(
        'sale.order', string='Pedido', required=True, index=True,
        ondelete='cascade', tracking=True)
    order_line_ids = fields.One2many(
        'sale.order.line', 'chart_contract_id', string='Líneas contratadas')
    # Variante principal (informativa): la primera línea del contrato.
    product_id = fields.Many2one(
        'product.product', string='Variante principal',
        compute='_compute_product_id', store=True, readonly=False)
    service_type = fields.Selection(
        selection=[
            ('one_time', 'Cargo único'),
            ('subscription', 'Recurrente'),
        ],
        string='Tipo de cargo', required=True, default='one_time',
        compute='_compute_service_type', store=True, readonly=False)
    periodicity = fields.Selection(
        selection=[
            ('none', 'Cargo único'),
            ('monthly', 'Mensual'),
            ('quarterly', 'Trimestral'),
            ('annual', 'Anual'),
        ],
        string='Periodicidad', compute='_compute_periodicity', store=True,
        readonly=False)

    # ------------------------------------------------------------ estados
    state = fields.Selection(
        selection=[
            ('draft', 'Borrador'),
            ('pending_verification', 'Pendiente de verificación'),
            ('pending_preparation', 'Pendiente de preparación'),
            ('in_preparation', 'En preparación'),
            ('pending_environment_check', 'Comprobación de entorno'),
            ('pending_activation_approval', 'Pendiente de aprobación de activación'),
            ('active', 'Activo'),
            ('on_hold', 'En pausa'),
            ('canceled', 'Cancelado'),
        ],
        string='Estado', default='draft', required=True, tracking=True, copy=False,
        help='Contratación → verificación y datos → preparación → comprobación de '
             'entorno → activación real (aprobación manual).')
    # Etiqueta orientada al cliente (los estados técnicos internos no se publican).
    state_label = fields.Char(
        string='Estado (cliente)', compute='_compute_state_label', store=True)

    # --------------------------------------------------- verificación/datos
    email_verified = fields.Boolean(
        string='Email verificado', default=False, copy=False, tracking=True)
    email_verified_date = fields.Datetime(string='Email verificado el', readonly=True, copy=False)
    email_verified_recipient = fields.Char(
        string='Email verificado (destinatario)', readonly=True, copy=False,
        help='Dirección de correo que realmente se verificó (evidencia).')
    email_verification_token = fields.Char(
        string='Token de verificación', copy=False, readonly=True, index=True,
        help='Token aleatorio de un solo uso. Nunca se muestra en el portal ni en logs.')
    email_verification_token_expiry = fields.Datetime(
        string='Token válido hasta', readonly=True, copy=False)
    email_verification_sent_at = fields.Datetime(
        string='Último envío', readonly=True, copy=False)
    email_verification_send_count = fields.Integer(
        string='Reenvíos', default=0, readonly=True, copy=False)
    data_complete = fields.Boolean(
        string='Datos completos', compute='_compute_data_complete', store=True)
    data_missing = fields.Char(
        string='Datos faltantes', compute='_compute_data_complete', store=True)

    data_completion_pct = fields.Float(
        string='% datos completados', compute='_compute_data_complete',
        store=True, digits=(3, 1),
        help='Porcentaje de campos requeridos completados (informativo para el cliente).')


    # ------------------------------------------------------------ económica
    amount_untaxed = fields.Monetary(string='Base', compute='_compute_amounts', store=True,
                                     currency_field='currency_id')
    amount_tax = fields.Monetary(string='Impuestos', compute='_compute_amounts', store=True,
                                 currency_field='currency_id')
    amount_total = fields.Monetary(string='Total contratado', compute='_compute_amounts',
                                   store=True, currency_field='currency_id')
    amount_recurring = fields.Monetary(
        string='Importe recurrente', compute='_compute_amounts', store=True,
        currency_field='currency_id',
        help='Suma de las líneas recurrentes del contrato (no se factura en esta entrega).')
    amount_one_time = fields.Monetary(
        string='Importe único', compute='_compute_amounts', store=True,
        currency_field='currency_id')
    payment_status = fields.Selection(
        selection=[
            ('not_required', 'Sin pago inmediato'),
            ('pending', 'Pago pendiente'),
            ('paid', 'Pagado'),
        ],
        string='Estado de pago', default='not_required', required=True, tracking=True)

    # ------------------------------------------------------------- términos
    terms_accepted = fields.Boolean(
        string='Condiciones aceptadas', default=False, copy=False, tracking=True)
    terms_version = fields.Char(string='Versión de condiciones', copy=False)
    terms_accepted_datetime = fields.Datetime(string='Aceptadas el', copy=False)
    economic_terms_snapshot = fields.Text(
        string='Condiciones económicas (congeladas)', readonly=True, copy=False,
        help='Importes, variantes, cantidades y periodicidad capturados del pedido en el '
             'momento de la contratación. No se recalculan con el catálogo vigente.')
    # Componentes económicos ESTRUCTURADOS (motor de cobro futuro). Cada línea de
    # pedido se clasifica por separado como cargo único o recurrente.
    economic_component_ids = fields.One2many(
        'chart.service.economic.component', 'contract_id',
        string='Componentes económicos', readonly=True, copy=False)

    # ------------------------------------------------------------- fechas
    contract_date = fields.Datetime(string='Fecha de contratación', readonly=True, copy=False,
                                    default=fields.Datetime.now)
    # Plazo objetivo de 72 h: se persiste como Datetime (UTC). NUNCA se usa Date
    # para medir un plazo exacto de horas. No activa nada por sí solo.
    activation_due_at = fields.Datetime(
        string='Activación prevista (72 h)', compute='_compute_activation_due_at',
        store=True, readonly=False,
        help='Plazo OBJETIVO de entrega (72 h desde la contratación), guardado en '
             'UTC. No activa nada por sí solo.')
    # Cuándo la solución quedó lista para activar (preparación + entorno OK).
    ready_at = fields.Datetime(
        string='Lista para activar el', readonly=True, copy=False,
        help='Momento (UTC) en que la preparación terminó y el entorno se comprobó, '
             'quedando la solución lista para la activación autorizada.')
    # Alias de lectura para compatibilidad (misma fecha objetivo de 72 h).
    activation_target_date = fields.Datetime(
        string='Objetivo de activación (72 h)', compute='_compute_activation_target_date',
        readonly=True,
        help='Alias de activation_due_at (plazo objetivo de 72 h).')
    activation_date = fields.Date(string='Fecha de activación real', readonly=True, copy=False,
                                  tracking=True)
    activation_note = fields.Text(string='Nota de activación', readonly=True, copy=False)
    activated_by = fields.Many2one('res.users', string='Activado por', readonly=True, copy=False)

    # ---------------------------------------------------------- preparación
    task_ids = fields.One2many('chart.provisioning.task', 'contract_id', string='Tareas')
    task_total = fields.Integer(compute='_compute_task_counts', string='Tareas totales')
    task_done = fields.Integer(compute='_compute_task_counts', string='Tareas hechas')
    task_progress = fields.Float(compute='_compute_task_counts', string='% preparación')
    environment_checked = fields.Boolean(string='Entorno comprobado', default=False)
    environment_checked_date = fields.Datetime(readonly=True, copy=False)
    environment_checked_by = fields.Many2one(
        'res.users', string='Entorno comprobado por', readonly=True, copy=False)
    environment_url = fields.Char(
        string='URL del entorno validada', readonly=True, copy=False,
        help='URL del entorno operativo del cliente, validada y permitida. '
             'El portal comercial y el entorno del cliente son recursos distintos.')
    environment_note = fields.Text(
        string='Nota de comprobación del entorno', readonly=True, copy=False)
    
    # ---------------------------------------------------------- aprovisionamiento
    provisioning_job_id = fields.Many2one(
        'chart.provisioning.job', string='Trabajo de aprovisionamiento',
        ondelete='set null', index=True,
        help='Trabajo de aprovisionamiento que creó el entorno para este contrato.')

    # Campos de conveniencia para el portal (se calculan desde el job)
    provisioning_state = fields.Char(
        compute='_compute_provisioning_state', string='Estado de entorno',
        store=True)
    provisioning_url = fields.Char(
        compute='_compute_provisioning_url', string='URL del entorno',
        store=True)
    provisioning_ready = fields.Boolean(
        compute='_compute_provisioning_ready', string='Entorno listo',
        store=True)
    provisioning_progress = fields.Text(
        compute='_compute_provisioning_progress', string='Progreso de entorno',
        store=True)

    # ---------------------------------------------------------- facturación
    billing_period_ids = fields.One2many('chart.service.billing.period', 'contract_id',
                                         string='Períodos facturables')
    billing_anchor_date = fields.Date(
        string='Ancla de facturación',
        help='Día de la activación real. Los períodos se calculan por MESES CALENDARIO '
             'desde aquí; nunca sumando 30 días ni recalculando desde la fecha del cron.')
    billing_generated = fields.Boolean(
        string='Facturación generada (esta entrega NO emite facturas)', default=False,
        compute='_compute_billing_generated', store=True, readonly=False)

    notes = fields.Text(string='Notas internas')
    last_activity = fields.Char(compute='_compute_last_activity', string='Última actividad')

    # Clave de idempotencia: mismo pedido + mismas líneas = mismo contrato.
    # Evita duplicados por doble clic o reintentos, a nivel de base de datos.
    dedup_key = fields.Char(string='Clave de idempotencia', required=True, readonly=True,
                            copy=False, index=True)

    _sql_constraints = [
        ('dedup_key_uniq', 'unique(dedup_key)',
         'Ese contrato ya fue creado con estas líneas del pedido. '
         'No se duplica la contratación.'),
    ]

    # =================================================================== computes
    @api.depends('state')
    def _compute_state_label(self):
        """Traduce el estado técnico a una etiqueta orientada al cliente."""
        labels = {
            'draft': 'Completa tus datos',
            'pending_verification': 'Completa tus datos',
            'pending_preparation': 'Preparando tu solución',
            'in_preparation': 'Preparando tu solución',
            'pending_environment_check': 'Preparando tu solución',
            'pending_activation_approval': 'Lista para activar',
            'active': 'Activa',
            'on_hold': 'Requiere atención',
            'canceled': 'Cancelada',
        }
        for contract in self:
            contract.state_label = labels.get(contract.state, contract.state)

    @api.depends('name', 'partner_id')
    def _compute_display_name(self):
        for contract in self:
            contract.display_name = f"{contract.name or _('Nuevo')} · {contract.partner_id.display_name or ''}"

    @api.depends('sale_order_id.currency_id', 'company_id.currency_id')
    def _compute_currency_id(self):
        for contract in self:
            contract.currency_id = (contract.sale_order_id.currency_id
                                    or contract.company_id.currency_id)

    @api.depends('sale_order_id.partner_id')
    def _compute_from_order(self):
        for contract in self:
            if contract.sale_order_id and not contract.partner_id:
                contract.partner_id = contract.sale_order_id.partner_id

    @api.depends('order_line_ids.product_id')
    def _compute_product_id(self):
        for contract in self:
            contract.product_id = contract.order_line_ids[:1].product_id

    @api.depends('order_line_ids.product_id.is_chart_service',
                 'order_line_ids.product_id.chart_service_periodicity')
    def _compute_periodicity(self):
        for contract in self:
            periodicity = contract.order_line_ids[:1].product_id.chart_service_periodicity
            contract.periodicity = periodicity or 'none'

    @api.depends('periodicity')
    def _compute_service_type(self):
        for contract in self:
            contract.service_type = 'one_time' if contract.periodicity == 'none' else 'subscription'

    @api.depends('order_line_ids.price_subtotal', 'order_line_ids.price_total',
                 'order_line_ids.product_uom_qty', 'order_line_ids.price_unit')
    def _compute_amounts(self):
        for contract in self:
            lines = contract.order_line_ids
            contract.amount_untaxed = sum(lines.mapped('price_subtotal'))
            contract.amount_tax = sum(lines.mapped('price_total')) - sum(lines.mapped('price_subtotal'))
            recurring = lines.filtered(lambda l: l.product_id.chart_service_periodicity != 'none')
            one_time = lines - recurring
            contract.amount_recurring = sum(recurring.mapped('price_total'))
            contract.amount_one_time = sum(one_time.mapped('price_total'))
            contract.amount_total = sum(lines.mapped('price_total'))

    @api.depends('contract_date')
    def _compute_activation_due_at(self):
        for contract in self:
            if contract.contract_date:
                contract.activation_due_at = contract.contract_date + timedelta(hours=ACTIVATION_TARGET_HOURS)

    @api.depends('activation_due_at')
    def _compute_activation_target_date(self):
        for contract in self:
            contract.activation_target_date = contract.activation_due_at

    @api.depends('email_verified', 'partner_id.name', 'partner_id.email',
                 'partner_id.phone', 'partner_id.city', 'partner_id.zip',
                 'partner_id.vat')
    def _compute_data_complete(self):
        for contract in self:
            partner = contract.partner_id
            required = {
                'nombre': bool(partner.name) and partner.name != partner.email,
                'email': bool(partner.email),
                'teléfono': bool(partner.phone),
                'ciudad': bool(partner.city),
                'código postal': bool(partner.zip),
                'RFC (VAT)': bool(partner.vat),
                'email verificado': contract.email_verified,
            }
            contract.data_complete = all(required.values())
            contract.data_missing = ', '.join(k for k, ok in required.items() if not ok)
            contract.data_completion_pct = round(
                100.0 * sum(1 for v in required.values() if v) / len(required), 1)

    @api.depends('task_ids.state')
    def _compute_task_counts(self):
        for contract in self:
            total = len(contract.task_ids)
            done = len(contract.task_ids.filtered(lambda t: t.state == 'done'))
            contract.task_total = total
            contract.task_done = done
            contract.task_progress = (done / total * 100.0) if total else 0.0

    @api.depends('billing_period_ids')
    def _compute_billing_generated(self):
        for contract in self:
            contract.billing_generated = bool(contract.billing_period_ids)

    @api.depends('state', 'task_ids.state', 'payment_status', 'activation_date')
    def _compute_last_activity(self):
        for contract in self:
            parts = [dict(contract._fields['state'].selection)[contract.state]]
            if contract.task_total:
                parts.append(f"preparación {contract.task_done}/{contract.task_total}")
            contract.last_activity = ' · '.join(parts)

    # =================================================================== factory
    @api.model
    def _prepare_dedup_key(self, order, lines):
        """Clave determinista: pedido + ids de las líneas contratadas."""
        line_ids = sorted(lines.ids)
        return f"SO{order.id}-" + "-".join(str(i) for i in line_ids)

    @api.model
    def _create_from_order(self, order):
        """Crea (o reutiliza) los contratos derivados de un pedido confirmado.

        - Agrupa las líneas por variante para que cada servicio tenga su propio
          contrato con preparación/activación independientes.
        - Idempotente: si el contrato ya existe para esas líneas, se regresa tal
          cual (doble clic / reintento no duplican nada).
        - Solo se consideran líneas de productos marcados como contratables.
        """
        service_lines = order.order_line.filtered(
            lambda l: l.product_id.is_chart_service and l.display_type not in ('line_note', 'line_section'))
        if not service_lines:
            return self.browse()

        contracts = self.browse()
        for product, lines in _groupby_lines(service_lines):
            dedup = self._prepare_dedup_key(order, lines)
            existing = self.sudo().search([('dedup_key', '=', dedup)], limit=1)
            if existing:
                contracts |= existing
                continue
            vals = self._prepare_from_order_values(order, product, lines, dedup)
            try:
                with self.env.cr.savepoint():
                    contracts |= self.sudo().create(vals)
            except Exception:
                # Posible carrera de doble clic: si el contrato ya existe (unique
                # dedup_key), se reutiliza; si no, se propaga el error real.
                found = self.sudo().search([('dedup_key', '=', dedup)], limit=1)
                if not found:
                    raise
                contracts |= found
        return contracts

    @api.model
    def _prepare_from_order_values(self, order, product, lines, dedup):
        first = lines[0]
        periodicity = product.chart_service_periodicity or 'none'
        snapshot = self._make_economic_snapshot(order, product, lines, periodicity)
        return {
            'name': self.env['ir.sequence'].next_by_code('chart.service.contract') or _('Nuevo'),
            'partner_id': order.partner_id.id,
            'sale_order_id': order.id,
            'order_line_ids': [(6, 0, lines.ids)],
            'product_id': product.id,
            'periodicity': periodicity,
            'service_type': 'one_time' if periodicity == 'none' else 'subscription',
            'contract_date': order.date_order or fields.Datetime.now(),
            'terms_accepted': bool(order.chart_terms_accepted),
            'terms_version': order.chart_terms_version or False,
            'terms_accepted_datetime': order.chart_terms_accepted_datetime or fields.Datetime.now(),
            'economic_terms_snapshot': snapshot,
            'dedup_key': dedup,
            'company_id': order.company_id.id,
            'currency_id': order.currency_id.id,
            'state': 'draft',
        }

    @api.model
    def _make_economic_snapshot(self, order, product, lines, periodicity):
        """Congela las condiciones económicas al momento de contratar."""
        cur = order.currency_id
        rows = []
        for line in lines:
            rows.append(
                f"- {line.product_id.display_name} | qty {line.product_uom_qty} | "
                f"unit {cur.format(line.price_unit)} | total {cur.format(line.price_total)}"
            )
        total = sum(lines.mapped('price_total'))
        return _(
            "Pedido %(order)s · %(date)s\n"
            "Servicio: %(service)s (%(periodicity)s)\n"
            "%(rows)s\n"
            "Total contratado: %(total)s\n"
            "Pago al contratar: ninguno (facturación diferida; no se emite factura en esta entrega).",
            order=order.name, date=(order.date_order or fields.Datetime.now()).date(),
            service=product.display_name, periodicity=periodicity,
            rows="\n".join(rows), total=cur.format(total),
        )

    def _sync_economic_components(self):
        """Crea los componentes económicos estructurados desde las líneas del pedido.

        Cada línea se clasifica por separado: cargo único (periodicidad 'none')
        o recurrente (mensual/trimestral/anual). Una solución con implementación
        + mensualidad genera dos componentes. Idempotente.
        """
        Component = self.env['chart.service.economic.component']
        for contract in self:
            existing = {c.order_line_id.id for c in contract.economic_component_ids}
            seq = 10
            for line in contract.order_line_ids:
                if line.id in existing:
                    continue
                periodicity = line.product_id.chart_service_periodicity or 'none'
                Component.create({
                    'contract_id': contract.id,
                    'sequence': seq,
                    'order_line_id': line.id,
                    'charge_type': 'one_time' if periodicity == 'none' else 'recurring',
                    'periodicity': periodicity,
                    'terms_version': contract.terms_version,
                    'terms_accepted_datetime': contract.terms_accepted_datetime,
                })
                seq += 10
        return True

    # =================================================================== create
    @api.model_create_multi
    def create(self, vals_list):
        contracts = super().create(vals_list)
        for contract in contracts:
            if not contract.name or contract.name == '/':
                contract.name = self.env['ir.sequence'].next_by_code('chart.service.contract') or _('Nuevo')
        contracts._sync_economic_components()
        return contracts

    # ===================================================== verificación email
    EMAIL_VERIFY_TTL_HOURS = 24
    EMAIL_VERIFY_MAX_SENDS = 5
    EMAIL_VERIFY_RESEND_COOLDOWN_MIN = 2

    def _generate_verification_token(self):
        """Token aleatorio de un solo uso (no predecible, no en logs)."""
        import secrets
        return secrets.token_urlsafe(32)

    def _verification_link(self):
        """Enlace de verificación con el token vigente."""
        self.ensure_one()
        if not self.email_verification_token:
            return False
        return '/my/soluciones/%s/verify?token=%s' % (
            self.id, self.email_verification_token)

    def action_send_verification_email(self):
        """Genera y envía el correo de verificación (reenvío limitado).

        - Token aleatorio de un solo uso con expiración.
        - Reenvío limitado (máx. EMAIL_VERIFY_MAX_SENDS) y con enfriamiento.
        - El token nunca se expone en el portal ni en logs.
        """
        for contract in self:
            if contract.email_verified:
                raise UserError(_("El email ya está verificado."))
            if not contract.partner_id.email:
                raise UserError(_("El cliente no tiene un email registrado."))
            now = fields.Datetime.now()
            if (contract.email_verification_sent_at
                    and contract.email_verification_send_count >= self.EMAIL_VERIFY_MAX_SENDS):
                raise UserError(_(
                    "Se alcanzó el límite de reenvíos de verificación. Contacta a soporte."))
            if (contract.email_verification_sent_at
                    and (now - contract.email_verification_sent_at).total_seconds()
                    < self.EMAIL_VERIFY_RESEND_COOLDOWN_MIN * 60):
                raise UserError(_("Espera unos minutos antes de reenviar la verificación."))
            token = self._generate_verification_token()
            expiry = now + timedelta(hours=self.EMAIL_VERIFY_TTL_HOURS)
            contract.write({
                'email_verification_token': token,
                'email_verification_token_expiry': expiry,
                'email_verification_sent_at': now,
                'email_verification_send_count': contract.email_verification_send_count + 1,
            })
            self._send_verification_mail(contract)
        return True

    def _send_verification_mail(self, contract):
        """Envía el correo de verificación al destinatario (buzón interceptado en staging)."""
        self.ensure_one()
        link = contract._verification_link()
        body = _(
            "Hola %(name)s,\n\n"
            "Para completar la verificación de tu correo y continuar con la "
            "preparación de tu servicio, abre este enlace:\n%(link)s\n\n"
            "El enlace es de un solo uso y caduca en %(hours)s horas.\n"
            "Si no solicitaste esto, ignora este mensaje.",
            name=contract.partner_id.name, link=link, hours=self.EMAIL_VERIFY_TTL_HOURS)
        mail = self.env['mail.mail'].create({
            'subject': _('Verifica tu correo · Grupo Chart'),
            'body_html': '<p>%s</p>' % body.replace('\n', '<br/>'),
            'email_from': self.env.company.email or 'no-reply@chart.lat',
            'email_to': contract.partner_id.email,
            'auto_delete': False,
        })
        # En staging el correo se envía a un buzón interceptado (mailcatcher/sink).
        mail.send()

    def action_verify_email(self, token):
        """Valida el token de un solo uso y marca el email como verificado.

        - Token correcto, vigente y no usado.
        - Registra destinatario y fecha (evidencia).
        - Consume el token (un solo uso).
        - NO activa ningún servicio: solo confirma la verificación.
        """
        self.ensure_one()
        if self.email_verified:
            return True
        if not token or not self.email_verification_token:
            raise UserError(_("No hay una verificación pendiente para este contrato."))
        import secrets
        if not secrets.compare_digest(token, self.email_verification_token):
            raise UserError(_("El enlace de verificación no es válido."))
        if (not self.email_verification_token_expiry
                or self.email_verification_token_expiry < fields.Datetime.now()):
            raise UserError(_("El enlace de verificación ha caducado. Solicita uno nuevo."))
        self.write({
            'email_verified': True,
            'email_verified_date': fields.Datetime.now(),
            'email_verified_recipient': self.partner_id.email,
            'email_verification_token': False,
            'email_verification_token_expiry': False,
        })
        return True

    def _invalidate_email_verification(self):
        """Invalida la verificación si cambia el correo del destinatario."""
        for contract in self:
            if (contract.email_verified
                    and contract.email_verified_recipient
                    and contract.partner_id.email
                    and contract.email_verified_recipient != contract.partner_id.email):
                contract.write({
                    'email_verified': False,
                    'email_verified_date': False,
                    'email_verified_recipient': False,
                    'email_verification_token': False,
                    'email_verification_token_expiry': False,
                    'email_verification_send_count': 0,
                })

    # =================================================================== actions
    def action_confirm_contract(self):
        """Contratación confirmada: el servicio queda Pendiente de preparación.

        Es el único estado inicial tras la confirmación del pedido. No activa,
        no factura, no simula tareas.
        """
        for contract in self:
            if contract.state != 'draft':
                raise UserError(_("Solo un contrato en borrador puede confirmarse."))
            if not contract.order_line_ids:
                raise UserError(_("El contrato no tiene líneas de pedido."))
            contract.write({
                'state': 'pending_preparation',
                'payment_status': 'not_required',
            })
            contract.message_post(
                body=_("Contratación confirmada. El servicio queda "
                       "<b>Pendiente de preparación</b>. No se cobró nada y no se "
                       "emitió ninguna factura."),
                message_type='notification', subtype_xmlid='mail.mt_note')
        return True

    def action_start_preparation(self):
        """Abre la preparación DESPUÉS de tener verificación/datos, nunca después
        de declarar activo."""
        for contract in self:
            if contract.state not in ('pending_preparation', 'pending_verification'):
                raise UserError(_("La preparación solo inicia desde 'Pendiente de preparación'."))
            contract.write({'state': 'in_preparation'})
            contract._ensure_preparation_tasks()
        return True

    def action_mark_environment_checked(self, environment_url=None, environment_note=None):
        """Registra la comprobación REAL del entorno y deja la solución lista.

        Exige que todas las tareas estén realmente completadas (con evidencia) y
        una URL de entorno validada. Al pasar, la solución queda 'Lista para
        activar' (ready_at) y se registra responsable y fecha.
        """
        for contract in self:
            if contract.state != 'in_preparation':
                raise UserError(_("Primero debe estar en preparación."))
            pending = contract.task_ids.filtered(lambda t: t.state != 'done')
            if pending:
                raise UserError(_(
                    "Faltan %s tareas de preparación por completar realmente: %s",
                    len(pending), ', '.join(pending.mapped('name'))))
            url = environment_url or contract.environment_url
            if not url:
                raise UserError(_(
                    "Debes indicar la URL del entorno operativo del cliente que se "
                    "comprobó. El portal comercial y el entorno del cliente son "
                    "recursos distintos."))
            url = self._validate_environment_url(url)
            contract.write({
                'state': 'pending_activation_approval',
                'environment_checked': True,
                'environment_checked_date': fields.Datetime.now(),
                'environment_checked_by': self.env.uid,
                'environment_url': url,
                'environment_note': environment_note or contract.environment_note,
                'ready_at': fields.Datetime.now(),
            })
        return True

    def _validate_environment_url(self, url):
        """Valida que la URL del entorno sea http(s) y esté en un dominio permitido.

        Evita enlaces o solicitudes a URLs arbitrarias (p. ej. javascript:, file:,
        localhost, IPs internas o dominios no autorizados).
        """
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            raise UserError(_("La URL del entorno debe ser http(s)."))
        if not parsed.netloc:
            raise UserError(_("La URL del entorno no es válida."))
        host = parsed.hostname or ''
        if host in ('localhost', '127.0.0.1', '::1') or host.endswith('.local'):
            raise UserError(_("La URL del entorno no puede apuntar a un host local."))
        # Dominios permitidos para entornos operativos de clientes.
        allowed = self.env['ir.config_parameter'].get_param(
            'chart_service_commerce.allowed_environment_domains', '')
        if allowed:
            allowed_hosts = {h.strip().lower() for h in allowed.split(',') if h.strip()}
            if allowed_hosts and not any(host == h or host.endswith('.' + h)
                                         for h in allowed_hosts):
                raise UserError(_(
                    "El dominio %s no está en la lista de entornos permitidos.", host))
        return url

    def action_activate(self):
        """ACTIVACIÓN REAL: solo manual, solo con entorno comprobado.

        Registra el ancla de facturación (fecha de activación) desde la que se
        calcularán los períodos por meses calendario. No emite facturas.
        """
        for contract in self:
            if contract.state != 'pending_activation_approval':
                raise UserError(_(
                    "No se puede activar: el contrato está en '%s'. "
                    "Requiere preparación completa y comprobación de entorno.",
                    dict(contract._fields['state'].selection)[contract.state]))
            if not contract.data_complete:
                raise UserError(_("Faltan datos del cliente: %s", contract.data_missing))
            if not contract.environment_checked:
                raise UserError(_("La comprobación del entorno no está registrada."))
            if not contract.environment_url:
                raise UserError(_(
                    "No hay una URL de entorno validada. No se puede activar sin "
                    "una comprobación real del entorno."))
            contract.write({
                'state': 'active',
                'activation_date': fields.Date.context_today(contract),
                'billing_anchor_date': fields.Date.context_today(contract),
                'activated_by': self.env.uid,
            })
            contract.message_post(body=_(
                "Servicio ACTIVADO manualmente por %(user)s. "
                "Ancla de facturación: %(anchor)s. No se ha emitido ninguna factura.",
                user=self.env.user.name, anchor=contract.billing_anchor_date))
        return True

    def action_hold(self):
        for contract in self:
            contract.write({'state': 'on_hold'})
        return True

    def action_cancel(self):
        for contract in self:
            contract.write({'state': 'canceled'})
        return True

    def action_generate_draft_invoices(self):
        """Genera borradores de factura para los períodos sin borrador (staging)."""
        periods = self.mapped('billing_period_ids').filtered(lambda p: not p.move_id)
        if not periods:
            raise UserError(_("No hay períodos sin borrador de factura."))
        return periods.action_generate_draft_invoice()

    def action_reset_draft(self):
        """Vuelve a borrador sin borrar nada (reversible).

        Un contrato ACTIVO no puede volver a borrador por una acción ordinaria:
        el ancla de facturación y la activación ya tienen efectos. Cualquier
        corrección debe ser explícita y trazada (p. ej. cancelar y rehacer).
        """
        for contract in self:
            if contract.state == 'active':
                raise UserError(_(
                    "Un contrato ACTIVO no puede volver a borrador por una acción "
                    "ordinaria. Si necesitas corregirlo, cancélalo y crea uno nuevo "
                    "de forma explícita y trazada."))
            contract.write({'state': 'draft', 'environment_checked': False,
                            'environment_checked_date': False})
        return True

    def write(self, vals):
        """Bloquea cambios ordinarios del ancla y del estado una vez activo.

        Tras la activación, el ancla de facturación y la fecha de activación no
        deben alterarse por una edición normal: cualquier corrección debe ser
        explícita (contexto chart_allow_anchor_correction) y trazada.
        """
        if 'billing_anchor_date' in vals or 'activation_date' in vals:
            active = self.filtered(lambda c: c.state == 'active')
            if active and not self.env.context.get('chart_allow_anchor_correction'):
                raise UserError(_(
                    "No se puede modificar el ancla de facturación ni la fecha de "
                    "activación de un contrato ACTIVO por una edición ordinaria. "
                    "Cualquier corrección debe ser explícita y trazada."))
        return super().write(vals)

    # =================================================================== tasks
    def _ensure_preparation_tasks(self):
        """Crea las tareas de preparación AL INICIAR la preparación (no después
        de activar). No las marca hechas."""
        self.ensure_one()
        if self.task_ids:
            return self.task_ids
        steps = [
            ('Verificación de identidad y datos de contacto', 'data_verification'),
            ('Recolección de accesos e integraciones requeridas', 'access_setup'),
            ('Importación de datos del cliente', 'data_import'),
            ('Configuración de usuario en el entorno del servicio', 'user_setup'),
            ('Onboarding y capacitación', 'onboarding'),
        ]
        Task = self.env['chart.provisioning.task']
        tasks = Task.browse()
        for seq, (label, code) in enumerate(steps, start=1):
            tasks |= Task.create({
                'name': label, 'code': code, 'contract_id': self.id,
                'sequence': seq, 'state': 'pending',
                'responsible_id': self.env.user.id,
            })
        return tasks

    # ------------------------------------------------------------------ provisioning helpers
    @api.depends('provisioning_job_id.state', 'provisioning_job_id.environment_url',
                 'provisioning_job_id.progress_message', 'provisioning_job_id.error_message',
                 'provisioning_job_id.retries_count', 'provisioning_job_id.max_retries',
                 'provisioning_job_id.provisioning_done')
    def _compute_provisioning_state(self):
        for contract in self:
            job = contract.provisioning_job_id
            contract.provisioning_state = job.state if job else 'none'

    @api.depends('provisioning_job_id.environment_url')
    def _compute_provisioning_url(self):
        for contract in self:
            job = contract.provisioning_job_id
            contract.provisioning_url = job.environment_url if job else False

    @api.depends('provisioning_job_id.state', 'provisioning_job_id.environment_url')
    def _compute_provisioning_ready(self):
        # Un trabajo NO está "listo" por tener una URL; exige estado técnico
        # 'ready' (verificado contra el recurso real) y un destino autorizado.
        for contract in self:
            job = contract.provisioning_job_id
            contract.provisioning_ready = bool(
                job and job.state == 'ready' and job.environment_url)

    @api.depends('provisioning_job_id.state', 'provisioning_job_id.progress_message',
                 'provisioning_job_id.error_message', 'provisioning_job_id.retries_count',
                 'provisioning_job_id.max_retries', 'provisioning_job_id.provisioning_done')
    def _compute_provisioning_progress(self):
        for contract in self:
            job = contract.provisioning_job_id
            if not job:
                contract.provisioning_progress = _(
                    'Aún no se ha iniciado la preparación del entorno.')
            elif job.state == 'ready':
                date_str = ''
                if job.provisioning_done:
                    date_str = job.provisioning_done.strftime('%d/%m/%Y %H:%M')
                contract.provisioning_progress = _(
                    'Entorno creado y verificado el %s.') % date_str
            elif job.state == 'failed':
                contract.provisioning_progress = _(
                    'Error: %s (reintentos: %d/%d)'
                    ) % (job.error_message or _('Sin detalles'),
                         job.retries_count, job.max_retries)
            elif job.state == 'provisioning':
                contract.provisioning_progress = (
                    job.progress_message or _('En proceso de creación del entorno...'))
            elif job.state == 'approved':
                contract.provisioning_progress = _(
                    'Aprobado. Esperando inicio de aprovisionamiento.')
            else:
                contract.provisioning_progress = _('Estado: %s') % job.state
