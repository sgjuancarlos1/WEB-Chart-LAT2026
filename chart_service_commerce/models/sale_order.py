# -*- coding: utf-8 -*-
"""Extiende el pedido nativo: condiciones aceptadas + contratos derivados.

No se crea ningún segundo pedido ni una autenticación paralela: el carrito de
la sesión ES el pedido. Al confirmar, se derivan los contratos desde las líneas.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

CHART_TERMS_VERSION = '2026-09-v1'


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    chart_terms_accepted = fields.Boolean(
        string='Condiciones de contratación aceptadas', copy=False, readonly=False,
        default=False)
    chart_terms_version = fields.Char(string='Versión de condiciones', copy=False, readonly=False)
    chart_terms_accepted_datetime = fields.Datetime(
        string='Condiciones aceptadas el', copy=False, readonly=False)
    chart_contract_ids = fields.One2many('chart.service.contract', 'sale_order_id',
                                         string='Contratos de servicio')
    chart_contract_count = fields.Integer(compute='_compute_chart_contract_count',
                                          string='Nº de contratos')
    chart_has_services = fields.Boolean(
        string='Contiene servicios contratables',
        compute='_compute_chart_has_services', store=True,
        help='Al menos una línea corresponde a un producto marcado como servicio '
             'contratable (sin pago inmediato).')
    chart_only_services = fields.Boolean(
        string='Solo servicios contratables',
        compute='_compute_chart_has_services', store=True)
    chart_service_line_ids = fields.One2many(
        'sale.order.line', 'order_id', string='Líneas de servicio',
        compute='_compute_chart_service_line_ids')

    @api.depends('order_line.product_id.is_chart_service')
    def _compute_chart_service_line_ids(self):
        for order in self:
            order.chart_service_line_ids = order.order_line.filtered(
                lambda l: l.product_id.is_chart_service
                and l.display_type not in ('line_note', 'line_section'))

    @api.depends('order_line.product_id.is_chart_service')
    def _compute_chart_has_services(self):
        for order in self:
            service_lines = order.order_line.filtered(
                lambda l: l.product_id.is_chart_service
                and l.display_type not in ('line_note', 'line_section'))
            real_lines = order.order_line.filtered(
                lambda l: l.display_type not in ('line_note', 'line_section'))
            order.chart_has_services = bool(service_lines)
            order.chart_only_services = bool(service_lines) and service_lines == real_lines

    @api.depends('chart_contract_ids')
    def _compute_chart_contract_count(self):
        for order in self:
            order.chart_contract_count = len(order.chart_contract_ids)

    # ------------------------------------------------------------------ terms
    def chart_accept_terms(self, version=CHART_TERMS_VERSION):
        """Registra persistentemente la aceptación de condiciones."""
        vals = {
            'chart_terms_accepted': True,
            'chart_terms_version': version,
            'chart_terms_accepted_datetime': fields.Datetime.now(),
        }
        changed = self.browse()
        for order in self:
            if not order.chart_terms_accepted or order.chart_terms_version != version:
                order.write(vals)
                changed |= order
        return changed

    # ---------------------------------------------------------------- checkout
    def chart_validate_checkout_confirmation(self):
        """Validación EN SERVIDOR de la confirmación de contratación Chart.

        - Exige carrito con líneas reales.
        - Exige términos aceptados (consentimiento persistido).
        - Exige presencia de servicios contratables.
        - UN carrito MIXTO (servicios + productos ordinarios) NO se confirma
          aquí: los productos ordinarios exigen su flujo de pago nativo. El
          cliente debe separar el carrito; nunca se confirman productos
          ordinarios sin pago a través de esta vía.
        """
        for order in self:
            real_lines = order.order_line.filtered(
                lambda l: l.display_type not in ('line_note', 'line_section'))
            if not real_lines:
                raise UserError(_("El carrito está vacío."))
            if not order.chart_terms_accepted:
                raise UserError(_(
                    "Debes aceptar las condiciones de contratación para continuar."))
            if not order.chart_has_services:
                raise UserError(_(
                    "El carrito no contiene ningún servicio contratable. "
                    "Usa el flujo de compra normal con pago."))
            if not order.chart_only_services:
                raise UserError(_(
                    "Este carrito mezcla servicios contratables con productos "
                    "ordinarios. Los productos ordinarios requieren pago: "
                    "finalízalos por el checkout normal y contrata los "
                    "servicios en un carrito aparte."))
        return True

    # ---------------------------------------------------------------- confirm
    def action_confirm(self):
        """Confirma el pedido nativo y deriva los contratos SIN SIMULAR nada.

        Control transaccional: si la creación de contratos falla, el pedido no
        queda confirmado a medias. Idempotente ante doble clic.
        """
        res = super().action_confirm()
        ServiceContract = self.env['chart.service.contract']
        for order in self:
            if not order.chart_has_services:
                continue
            contracts = ServiceContract._create_from_order(order)
            to_confirm = contracts.filtered(lambda c: c.state == 'draft')
            if to_confirm:
                to_confirm.action_confirm_contract()
            _logger.info(
                "Contratos derivados del pedido %s: %s (estados: %s)",
                order.name, [c.name for c in contracts], [c.state for c in contracts])
        return res

    def action_view_chart_contracts(self):
        action = self.env['ir.actions.act_window']._for_xml_id(
            'chart_service_commerce.action_chart_service_contract')
        contracts = self.chart_contract_ids
        if len(contracts) == 1:
            action.update({
                'res_model': 'chart.service.contract',
                'view_mode': 'form',
                'res_id': contracts.id,
                'domain': [],
            })
        else:
            action['domain'] = [('id', 'in', contracts.ids)]
        return action


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    chart_contract_id = fields.Many2one(
        'chart.service.contract', string='Contrato de servicio',
        ondelete='cascade', index=True)
