# -*- coding: utf-8 -*-
"""Portal "Mis soluciones" + confirmación de contratación sin pago.

Reutiliza íntegramente el flujo nativo:
- registro/login: auth_signup + website_sale (no hay autenticación paralela)
- carrito/pedido: request.cart -> el MISMO sale.order de la sesión
- confirmación: sale.order.action_confirm (heredado, no reemplazado)
"""
import logging

from odoo import _, http
from odoo.exceptions import MissingError, UserError
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager
from odoo.addons.website_sale.controllers.main import WebsiteSale

_logger = logging.getLogger(__name__)


class WebsiteSaleChart(WebsiteSale):
    """Checkout de servicios: acepta condiciones y confirma sin cobrar."""

    @http.route(['/shop/chart/confirm'], type='http', auth='public', website=True,
                readonly=False, sitemap=False)
    def chart_confirm_contract(self, accept_terms=None, **post):
        """Confirma la contratación SIN iniciar ningún pago.

        La confirmación comercial exige identidad autenticada. Un visitante
        anónimo (usuario público) NO puede confirmar ni asignar contratos: esta
        ruta solo conserva el carrito en la sesión y lo redirige al registro o
        inicio de sesión. Tras autenticarse, el carrito se reasigna al partner
        y el cliente vuelve a confirmar desde /shop/cart.

        Validación en servidor: carrito con líneas, términos aceptados,
        presencia de servicios contratables. Idempotente ante doble clic y
        concurrencia: si el pedido ya está confirmado no se duplica la
        contratación y el cliente recibe una respuesta comprensible.
        """
        order_sudo = request.cart
        if not order_sudo or not order_sudo.order_line:
            return request.redirect('/shop')
        # La contratación exige identidad autenticada: nunca confirmar ni
        # asignar contratos al usuario público.
        if request.env.user._is_public():
            request.session['sale_last_order_id'] = order_sudo.id
            return request.redirect('/web/login?redirect=/shop/cart')
        if order_sudo.state != 'draft':
            # Ya fue contratado antes (doble clic / reintento): no se duplica.
            request.session['sale_last_order_id'] = order_sudo.id
            return request.redirect('/my/soluciones')
        if not accept_terms:
            return request.render('chart_service_commerce.chart_checkout_template', {
                'order': order_sudo,
                'error': _('Debes aceptar las condiciones de contratación para continuar.'),
            })
        if not order_sudo.chart_has_services:
            return request.render('chart_service_commerce.chart_checkout_template', {
                'order': order_sudo,
                'error': _('El carrito no contiene ningún servicio contratable. '
                           'Usa el flujo de compra normal con pago.'),
            })
        order_sudo.chart_accept_terms()
        try:
            order_sudo.action_confirm()
        except UserError:
            # Solo se absorbe el conflicto esperado de doble confirmación
            # (otro proceso confirmó entre la comprobación de estado y esta
            # confirmación). Si el pedido NO quedó confirmado, el error es real
            # y se propaga: no se ocultan fallos con una búsqueda de duplicados.
            if order_sudo.state != 'sale':
                raise
            request.session['sale_last_order_id'] = order_sudo.id
            return request.redirect('/my/soluciones')
        request.session['sale_last_order_id'] = order_sudo.id
        request.website.sale_reset()
        return request.redirect('/my/soluciones')


class CustomerPortalChartSolutions(CustomerPortal):
    """Portal "Mis soluciones": contratos del partner autenticado."""

    def _chart_contract_domain(self, partner, state=None):
        # Solo el titular autorizado (partner_id == user.partner_id) o un
        # contacto EXPLÍCITAMENTE autorizado. NO se usa child_of del
        # commercial_partner_id: eso daría acceso a todos los contactos de la
        # empresa sin autorización.
        domain = ['|',
                  ('partner_id', '=', partner.id),
                  ('authorized_partner_ids', 'in', [partner.id])]
        if state:
            domain.append(('state', '=', state))
        return domain

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        partner = request.env.user.partner_id
        Contract = request.env['chart.service.contract']
        if 'chart_contract_count' in counters:
            values['chart_contract_count'] = (
                Contract.search_count(self._chart_contract_domain(partner))
                if Contract.has_access('read') else 0)
        return values

    @http.route(['/my/soluciones', '/my/soluciones/page/<int:page>'],
                type='http', auth='user', website=True)
    def portal_my_solutions(self, state=None, page=1, **kw):
        partner = request.env.user.partner_id
        Contract = request.env['chart.service.contract']
        domain = self._chart_contract_domain(partner, state)
        values = self._prepare_portal_layout_values()
        total = Contract.search_count(domain) if Contract.has_access('read') else 0
        pager_values = portal_pager(
            url="/my/soluciones", total=total, page=page,
            step=self._items_per_page,
            url_args={'state': state} if state else None)
        contracts = Contract.search(
            domain, order='contract_date desc', limit=self._items_per_page,
            offset=pager_values['offset']) if Contract.has_access('read') else Contract
        values.update({
            'contracts': contracts,
            'page_name': 'chart_solutions',
            'pager': pager_values,
            'default_url': '/my/soluciones',
            'state_filter': state or '',
        })
        return request.render('chart_service_commerce.portal_my_solutions', values)

    @http.route(['/my/soluciones/<int:contract_id>'], type='http', auth='user',
                website=True)
    def portal_my_solution_detail(self, contract_id, **kw):
        """Búsqueda SIN sudo: la ACL de portal + la regla de propiedad deciden.

        Un contrato de otro cliente no aparece -> MissingError -> redirect.
        """
        values = self._prepare_portal_layout_values()
        try:
            contract = self._chart_contract_get(contract_id)
        except MissingError:
            return request.redirect('/my/soluciones')
        values.update({'contract': contract.sudo(), 'page_name': 'chart_solutions'})
        return request.render('chart_service_commerce.portal_my_solution_detail', values)

    def _chart_contract_get(self, contract_id):
        """Respeta la ACL: si no es legible por el usuario actual, MissingError."""
        Contract = self._request_get_model('chart.service.contract')
        contract = Contract.browse(int(contract_id)).exists()
        if not contract:
            raise MissingError(_("Este contrato no existe o fue eliminado."))
        # search sobre el mismo id: filtra por record rule del portal
        allowed = Contract.search_count([('id', '=', contract.id)])
        if not allowed:
            raise MissingError(_("No tienes acceso a este contrato."))
        return contract

    def _request_get_model(self, model_name):
        return request.env[model_name]

    @http.route(['/my/soluciones/<int:contract_id>/verify'], type='http',
                auth='public', website=True, sitemap=False)
    def portal_verify_email(self, contract_id, token=None, **kw):
        """Verifica el email desde el enlace del correo (token de un solo uso).

        - auth='public': el cliente puede abrir el enlace sin sesión.
        - El token se valida en servidor (un solo uso, vigente).
        - NO activa ningún servicio: solo confirma la verificación y muestra una
          página de confirmación. Un escáner automático de correo no activa nada.
        - El token nunca se expone en la página ni en logs.
        """
        contract = request.env['chart.service.contract'].sudo().browse(
            int(contract_id)).exists()
        if not contract:
            return request.not_found()
        error = False
        if not token:
            error = _('Falta el enlace de verificación.')
        else:
            try:
                contract.sudo().action_verify_email(token)
            except UserError as e:
                error = e.args[0] if e.args else _('No se pudo verificar el email.')
        return request.render('chart_service_commerce.portal_email_verify_result', {
            'contract': contract,
            'error': error,
            'verified': bool(contract.email_verified),
        })
