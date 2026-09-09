# -*- coding: utf-8 -*-
"""Endurecimiento de seguridad (bloque 2 del expediente).

Cubre:
- Activación del contrato restringida EN SERVIDOR al responsable (también por
  escritura directa / RPC, no solo por la vista).
- Estados, evidencia de verificación y ancla histórico congelados tras
  hold/cancel frente a ediciones ordinarias.
- Token de verificación de email ilegible por portal y usuario interno.
- Invalidación de verificación y tokens al cambiar o VACIAR el email.
- Carrito mixto: no se pueden confirmar productos ordinarios sin pago.
- Aislamiento de componentes económicos por compañía.
"""
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import ChartContractCommon


@tagged('post_install', '-at_install')
class TestSecurityHardening(ChartContractCommon):

    _seq = 0

    def _contract(self, email=None):
        type(self)._seq += 1
        email = email or 'h%d@sec-hard.com' % type(self)._seq
        partner = self._portal_partner('Cliente H%d' % type(self)._seq, email)
        order = self._make_order(partner, [(self.test_service.product_variant_id, 1)])
        order.action_confirm()
        return order.chart_contract_ids

    def _preparador(self):
        type(self)._seq += 1
        n = type(self)._seq
        return self.env['res.users'].create({
            'name': 'Preparador Sec %d' % n,
            'login': 'prep-sec-%d@test.com' % n,
            'email': 'prep-sec-%d@test.com' % n,
            'group_ids': [(6, 0, [
                self.env.ref('chart_service_commerce.group_chart_service_user').id])],
        })

    def _to_pending_activation(self, contract):
        contract.write({
            'state': 'pending_activation_approval',
            'environment_checked': True,
            'environment_url': 'https://cliente-h.test.chart.lat',
            'email_verified': True,
        })

    def test_01_activation_requires_manager_even_by_direct_write(self):
        """Un preparador no activa ni por acción ni por escritura directa."""
        contract = self._contract()
        self._to_pending_activation(contract)
        preparador = self._preparador()
        with self.assertRaises(UserError):
            contract.with_user(preparador).action_activate()
        with self.assertRaises(UserError):
            contract.with_user(preparador).write({'state': 'active'})

    def test_02_manager_activates_and_guard_does_not_block(self):
        """El responsable SÍ puede activar (el guard no bloquea el flujo legítimo)."""
        contract = self._contract()
        self._to_pending_activation(contract)
        contract.with_user(self.env.ref('base.user_admin')).action_activate()
        self.assertEqual(contract.state, 'active')
        self.assertTrue(contract.billing_anchor_date)

    def test_03_state_evidence_and_anchor_frozen_after_cancel(self):
        """Tras cancelar: estado, evidencia y ancla congelados."""
        contract = self._contract()
        contract.action_send_verification_email()
        contract.action_verify_email(contract.email_verification_token)
        contract.action_cancel()
        self.assertEqual(contract.state, 'canceled')
        with self.assertRaises(UserError):
            contract.write({'state': 'draft'})
        with self.assertRaises(UserError):
            contract.write({'billing_anchor_date': '2026-01-01',
                            'activation_date': '2026-01-01'})
        with self.assertRaises(UserError):
            contract.write({'email_verified_recipient': 'otro@test.com'})
        # la evidencia sigue intacta
        self.assertTrue(contract.email_verified)

    def test_04_state_evidence_frozen_after_hold(self):
        """Tras hold: el estado y la evidencia no cambian por edición ordinaria."""
        contract = self._contract()
        contract.action_send_verification_email()
        contract.action_hold()
        self.assertEqual(contract.state, 'on_hold')
        with self.assertRaises(UserError):
            contract.write({'state': 'active'})
        with self.assertRaises(UserError):
            contract.write({'email_verified_recipient': 'x@test.com'})

    def test_05_token_not_readable_by_portal_or_internal(self):
        """El token de verificación no es legible por portal ni interno (RPC)."""
        contract = self._contract()
        contract.action_send_verification_email()
        self.assertTrue(contract.email_verification_token)
        portal = contract.partner_id.user_ids[0]
        with self.assertRaises(AccessError):
            contract.with_user(portal).read(['email_verification_token'])
        type(self)._seq += 1
        n = type(self)._seq
        interno = self.env['res.users'].create({
            'name': 'Interno Sec %d' % n,
            'login': 'int-sec-%d@test.com' % n,
            'email': 'int-sec-%d@test.com' % n,
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        with self.assertRaises(AccessError):
            contract.with_user(interno).read(['email_verification_token'])

    def test_06_clearing_email_invalidates_verification(self):
        """Vaciar el email del partner invalida la verificación y los tokens."""
        contract = self._contract()
        contract.action_send_verification_email()
        contract.action_verify_email(contract.email_verification_token)
        self.assertTrue(contract.email_verified)
        contract.partner_id.write({'email': False})
        self.assertFalse(contract.email_verified)
        self.assertFalse(contract.email_verified_recipient)
        self.assertFalse(contract.email_verification_token)

    def test_07_mixed_cart_cannot_confirm_ordinary_products_without_payment(self):
        """Carrito mixto: la validación en servidor lo rechaza; los productos
        ordinarios solo salen por el flujo de pago nativo."""
        partner = self._portal_partner('Cliente Mixto', 'mixto@test.com')
        order = self._make_order(partner, [
            (self.test_service.product_variant_id, 1),
            (self.test_normal_product.product_variant_id, 2),
        ])
        order.chart_accept_terms()
        self.assertTrue(order.chart_has_services)
        self.assertFalse(order.chart_only_services)
        with self.assertRaises(UserError):
            order.chart_validate_checkout_confirmation()
        # nada se confirmó por esta vía: la orden sigue en borrador
        self.assertEqual(order.state, 'draft')

    def test_08_ordinary_only_cart_rejected(self):
        """Un carrito sin servicios no se confirma por la vía Chart."""
        partner = self._portal_partner('Cliente Ordinario', 'ordinario@test.com')
        order = self._make_order(partner, [
            (self.test_normal_product.product_variant_id, 1)])
        order.chart_accept_terms()
        self.assertFalse(order.chart_has_services)
        with self.assertRaises(UserError):
            order.chart_validate_checkout_confirmation()

    def test_09_components_isolated_by_company(self):
        """Los componentes económicos quedan aislados por compañía (interno)."""
        contract = self._contract()
        comps = contract.economic_component_ids
        self.assertTrue(comps)
        type(self)._seq += 1
        n = type(self)._seq
        company_b = self.env['res.company'].create({'name': 'Compañía B %d' % n})
        user_b = self.env['res.users'].create({
            'name': 'Usuario B %d' % n,
            'login': 'user-b-%d@test.com' % n,
            'email': 'user-b-%d@test.com' % n,
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
            'company_id': company_b.id,
            'company_ids': [(6, 0, [company_b.id])],
        })
        found = self.env['chart.service.economic.component'].with_user(user_b).search(
            [('id', 'in', comps.ids)])
        self.assertFalse(found)
        with self.assertRaises(AccessError):
            comps.with_user(user_b).read(['price_total'])
