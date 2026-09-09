# -*- coding: utf-8 -*-
"""Portal: el cliente completa sus datos y recupera su contratación (bloque 5).

Solo lo verificable por ORM. Login, navegación HTTP y entorno real quedan como
pendientes declarados (requieren navegador autorizado).
"""
from odoo.tests import tagged

from .common import ChartContractCommon


@tagged('post_install', '-at_install')
class TestPortalCompletion(ChartContractCommon):

    _seq = 0

    def _contract_with_minimal_partner(self):
        """Partner solo con nombre y email (faltan teléfono, ciudad, CP, RFC)."""
        type(self)._seq += 1
        n = type(self)._seq
        email = 'incompleto%d@test.com' % n
        user = self.env['res.users'].create({
            'name': 'Cliente Incompleto %d' % n,
            'login': email,
            'email': email,
            'partner_id': self.env['res.partner'].create({
                'name': 'Cliente Incompleto %d' % n,
                'email': email,
            }).id,
            'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])],
        })
        partner = user.partner_id
        order = self._make_order(partner, [(self.test_service.product_variant_id, 1)])
        order.action_confirm()
        return order.chart_contract_ids, user

    def test_01_missing_data_detected(self):
        """data_complete detecta los datos faltantes y los enumera."""
        contract, _ = self._contract_with_minimal_partner()
        self.assertFalse(contract.data_complete)
        self.assertTrue(contract.data_missing)

    def test_02_client_completes_required_data(self):
        """El cliente completa los datos requeridos y data_complete pasa a True."""
        contract, _ = self._contract_with_minimal_partner()
        contract.partner_id.write({
            'phone': '+52 55 1234 5678',
            'city': 'Ciudad de México',
            'zip': '06600',
            'vat': 'XAXX010101000',
        })
        # el requerimiento incluye el email verificado (sin verificación no hay
        # datos completos: así se diseñó)
        contract.action_send_verification_email()
        contract.action_verify_email(contract.email_verification_token)
        self.assertTrue(contract.data_complete)
        self.assertFalse(contract.data_missing)

    def test_03_contract_recoverable_by_its_owner(self):
        """El titular recupera su contratación (búsqueda como su usuario)."""
        contract, user = self._contract_with_minimal_partner()
        found = self.env['chart.service.contract'].with_user(user).search(
            [('id', '=', contract.id)])
        self.assertEqual(found, contract)
        # la verificación de email pendiente no bloquea la recuperación del registro
        self.assertFalse(contract.email_verified)
