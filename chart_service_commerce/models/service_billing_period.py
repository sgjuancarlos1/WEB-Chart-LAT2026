# -*- coding: utf-8 -*-
"""Períodos facturables PERSISTENTES y ÚNICOS.

Reglas corregidas vs. la propuesta anterior:
- Se calculan por MESES CALENDARIO desde el ancla de activación
  (billing_anchor_date), NO sumando 30 días y NO recalculando el ancla con la
  fecha del cron.
- Unicidad garantizada en base de datos: (contract_id, index) y
  (contract_id, date_start) no se repiten.
- Generación idempotente: un cron puede correr varias veces sin crear duplicados.
- EN ESTA ENTREGA NO SE EMITE, TIMBRA NI ENVÍA NINGUNA FACTURA:
  move_id permanece en False y 'invoiced' en False. El modelo solo deja el
  terreno preparado para una entrega futura.
"""
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

PERIOD_MONTHS = {'monthly': 1, 'quarterly': 3, 'annual': 12}


class ChartServiceBillingPeriod(models.Model):
    _name = 'chart.service.billing.period'
    _description = 'Período facturable de servicio'
    _order = 'contract_id, index'

    name = fields.Char(compute='_compute_name', store=True)
    contract_id = fields.Many2one('chart.service.contract', string='Contrato',
                                  required=True, ondelete='cascade', index=True)
    partner_id = fields.Many2one(related='contract_id.partner_id', store=True, index=True)
    company_id = fields.Many2one(related='contract_id.company_id', store=True)
    currency_id = fields.Many2one(related='contract_id.currency_id', store=True)
    index = fields.Integer(string='Nº de período', required=True)
    date_start = fields.Date(required=True, index=True)
    date_end = fields.Date(required=True)
    amount = fields.Monetary(string='Importe previsto', currency_field='currency_id')
    charge_type = fields.Selection(
        selection=[('one_time', 'Único'), ('recurring', 'Recurrente')],
        string='Tipo de cargo', required=True, default='recurring')
    invoiced = fields.Boolean(default=False, readonly=True)
    move_id = fields.Many2one('account.move', string='Factura (borrador)', readonly=True,
                              help='Borrador de factura (account.move en draft) generado '
                                   'para este período en staging. No timbrado ni enviado.')
    draft_invoice_generated = fields.Boolean(
        string='Borrador generado', default=False, readonly=True,
        help='True cuando se generó un borrador de factura para este período.')
    state = fields.Selection(
        selection=[('upcoming', 'Próximo'), ('open', 'Abierto'), ('closed', 'Cerrado')],
        compute='_compute_state', store=True)

    _period_index_uniq = models.Constraint(
        'UNIQUE(contract_id, index)',
        'Ya existe ese período para este contrato (no duplicar).')
    _period_start_uniq = models.Constraint(
        'UNIQUE(contract_id, date_start)',
        'Ya existe un período que empieza en esa fecha para este contrato.')
    _period_dates_check = models.Constraint(
        'CHECK(date_end >= date_start)',
        'El fin del período no puede ser anterior a su inicio.')

    @api.depends('index', 'date_start', 'date_end')
    def _compute_name(self):
        for period in self:
            period.name = _("Período %s (%s → %s)", period.index,
                            period.date_start or '-', period.date_end or '-')

    @api.depends('date_start', 'date_end')
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for period in self:
            if period.date_end and period.date_end < today:
                period.state = 'closed'
            elif period.date_start and period.date_start <= today:
                period.state = 'open'
            else:
                period.state = 'upcoming'

    # ------------------------------------------------------------------ calc
    @api.model
    def _period_boundary(self, anchor, k, months_per_cycle):
        """Frontera B(k) = anchor + relativedelta(months=k * meses_por_ciclo).

        CADA frontera se calcula directamente desde el ancla original. No se
        encadenan fechas (encadenar desplaza el ancla: 31 ene → 28 feb → 28 mar).
        """
        return anchor + relativedelta(months=k * months_per_cycle)

    @api.model
    def generate_periods(self, contract, count=3):
        """Crea de forma idempotente los próximos `count` períodos.

        Ancla = contract.billing_anchor_date (fecha de activación real). Si no
        hay ancla, NO se genera nada: no se inventa una fecha desde 'hoy'.

        Política de períodos (explícita):
          B(k) = anchor + relativedelta(months=k * meses_por_ciclo)
          período k = [B(k-1), B(k))   (inicio incluido, fin excluido)
          date_end (fin incluido) = B(k) - 1 día
          fecha de facturación vencida = B(k)

        Así no hay huecos, solapes ni pérdida del ancla. Ejemplo mensual con
        ancla 31 ene 2027: [31 ene, 27 feb] → [28 feb, 30 mar] → [31 mar, 29 abr].
        """
        if contract.periodicity == 'none' or not contract.billing_anchor_date:
            return self.browse()
        months = PERIOD_MONTHS.get(contract.periodicity)
        if not months:
            return self.browse()
        anchor = contract.billing_anchor_date
        Period = self.sudo()
        existing = {p.index: p for p in contract.billing_period_ids}
        created = Period.browse()
        for i in range(1, count + 1):
            if i in existing:
                # idempotente: ese período ya existe (mismo índice → mismas fechas)
                continue
            # fronteras calculadas DIRECTAMENTE desde el ancla, sin encadenar
            start = self._period_boundary(anchor, i - 1, months)
            end = self._period_boundary(anchor, i, months) - timedelta(days=1)
            vals = {
                'contract_id': contract.id,
                'index': i,
                'date_start': start,
                'date_end': end,
                'amount': contract.amount_recurring,
                'charge_type': 'recurring',
                'invoiced': False,
            }
            try:
                with contract.env.cr.savepoint():
                    created |= Period.create(vals)
            except Exception:
                # otro proceso ya creó exactamente este período: idempotente
                if Period.search_count([('contract_id', '=', contract.id),
                                        ('index', '=', i)]):
                    continue
                raise
        return created

    def action_generate_draft_invoice(self):
        """Genera un BORRADOR de factura (account.move en draft) para el período.

        - NO timbra, NO envía, NO marca pagado: solo deja el borrador listo.
        - Solo disponible en entornos donde esté habilitado explícitamente
          (config chart_service_commerce.enable_draft_invoicing = True, p. ej.
          staging). En producción permanece deshabilitado.
        - Idempotente: si el período ya tiene un borrador, no crea otro.
        """
        enabled = self.env['ir.config_parameter'].get_param(
            'chart_service_commerce.enable_draft_invoicing', 'False') == 'True'
        if not enabled:
            raise UserError(_(
                "La generación de borradores de factura está deshabilitada en este "
                "entorno. Se habilita explícitamente solo en staging."))
        for period in self:
            if period.move_id:
                continue  # ya tiene borrador (idempotente)
            contract = period.contract_id
            if not contract.partner_id:
                raise UserError(_("El contrato no tiene cliente."))
            journal = self._get_sale_journal(contract.company_id)
            move = self.env['account.move'].with_context(
                default_move_type='out_invoice').create({
                    'move_type': 'out_invoice',
                    'partner_id': contract.partner_id.id,
                    'journal_id': journal.id,
                    'invoice_date': period.date_end,
                    'company_id': contract.company_id.id,
                    'currency_id': contract.currency_id.id,
                    'ref': _('Período %s · %s', period.name, contract.name),
                    'invoice_line_ids': [(0, 0, self._draft_invoice_line_vals(period))],
                })
            period.write({'move_id': move.id, 'draft_invoice_generated': True})
        return True

    def _get_sale_journal(self, company):
        journal = self.env['account.journal'].search(
            [('type', '=', 'sale'), ('company_id', '=', company.id)], limit=1)
        if not journal:
            raise UserError(_("No hay un diario de ventas para la compañía."))
        return journal

    def _draft_invoice_line_vals(self, period):
        """Línea del borrador: producto del contrato, importe del período."""
        contract = period.contract_id
        product = contract.product_id
        account = product.property_account_income_id or product.categ_id.property_account_income_categ_id
        if not account:
            # En Odoo 19 las cuentas no tienen company_id (son compartidas).
            account = self.env['account.account'].search(
                [('account_type', '=', 'income')], limit=1)
        if not account:
            raise UserError(_("No hay una cuenta de ingresos configurada."))
        return {
            'name': _('%s · %s', contract.name, period.name),
            'product_id': product.id if product else False,
            'quantity': 1,
            'price_unit': period.amount,
            'account_id': account.id,
            'tax_ids': [(6, 0, product.taxes_id.ids)] if product else [(5, 0, 0)],
        }
