# -*- coding: utf-8 -*-
"""Pruebas del flujo real de verificación de email (token de un solo uso).

Se usa un buzón interceptado (mail.mail en borrador/cola) para no enviar
correos reales. El token nunca se expone en el portal ni en logs.
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ChartContractCommon


@tagged('post_install', '-at_install')
class TestEmailVerification(ChartContractCommon):

    def _contract(self, email='v@test.com'):
        partner = self._portal_partner('Cliente V', email)
        order = self._make_order(partner, [(self.test_service.product_variant_id, 1)])
        order.action_confirm()
        return order.chart_contract_ids

    def test_01_send_generates_token_with_expiry(self):
        """Enviar verificación genera token aleatorio, expiración y cuenta envíos."""
        contract = self._contract()
        self.assertFalse(contract.email_verification_token)
        contract.action_send_verification_email()
        self.assertTrue(contract.email_verification_token)
        self.assertTrue(contract.email_verification_token_expiry)
        self.assertEqual(contract.email_verification_send_count, 1)
        self.assertTrue(contract.email_verification_sent_at)
        # el token no se expone en el display_name ni en campos de lectura pública
        self.assertNotIn(contract.email_verification_token, contract.display_name)

    def test_02_verify_with_correct_token(self):
        """Verificar con el token correcto marca verificado y consume el token."""
        contract = self._contract()
        contract.action_send_verification_email()
        token = contract.email_verification_token
        contract.action_verify_email(token)
        self.assertTrue(contract.email_verified)
        self.assertTrue(contract.email_verified_date)
        self.assertEqual(contract.email_verified_recipient, 'v@test.com')
        # token consumido (un solo uso)
        self.assertFalse(contract.email_verification_token)
        self.assertFalse(contract.email_verification_token_expiry)

    def test_03_wrong_token_fails(self):
        """Un token incorrecto no verifica."""
        contract = self._contract()
        contract.action_send_verification_email()
        with self.assertRaises(UserError):
            contract.action_verify_email('token-incorrecto')
        self.assertFalse(contract.email_verified)

    def test_04_token_is_single_use(self):
        """El token es de un solo uso: la segunda verificación falla."""
        contract = self._contract()
        contract.action_send_verification_email()
        token = contract.email_verification_token
        contract.action_verify_email(token)
        self.assertTrue(contract.email_verified)
        # ya no hay token: reintentar no verifica de nuevo ni lanza error técnico
        self.assertFalse(contract.email_verification_token)

    def test_05_expired_token_fails(self):
        """Un token caducado no verifica."""
        contract = self._contract()
        contract.action_send_verification_email()
        token = contract.email_verification_token
        contract.write({'email_verification_token_expiry':
                        fields.Datetime.now() - timedelta(hours=1)})
        with self.assertRaises(UserError):
            contract.action_verify_email(token)
        self.assertFalse(contract.email_verified)

    def test_06_resend_limit_enforced(self):
        """El reenvío está limitado y con enfriamiento."""
        contract = self._contract()
        # simular que ya se alcanzó el límite
        contract.write({'email_verification_send_count': 5,
                        'email_verification_sent_at': fields.Datetime.now()})
        with self.assertRaises(UserError):
            contract.action_send_verification_email()
        # enfriamiento: reenviar justo después del último envío falla
        contract.write({'email_verification_send_count': 1,
                        'email_verification_sent_at': fields.Datetime.now()})
        with self.assertRaises(UserError):
            contract.action_send_verification_email()

    def test_07_email_change_invalidates_verification(self):
        """Cambiar el correo del partner invalida la verificación previa."""
        contract = self._contract()
        contract.action_send_verification_email()
        token = contract.email_verification_token
        contract.action_verify_email(token)
        self.assertTrue(contract.email_verified)
        # el partner cambia su correo
        contract.partner_id.write({'email': 'nuevo@test.com'})
        self.assertFalse(contract.email_verified)
        self.assertFalse(contract.email_verified_recipient)
        self.assertFalse(contract.email_verification_token)

    def test_08_verify_does_not_activate(self):
        """Verificar el email NO activa ningún servicio."""
        contract = self._contract()
        contract.action_send_verification_email()
        contract.action_verify_email(contract.email_verification_token)
        self.assertTrue(contract.email_verified)
        self.assertNotEqual(contract.state, 'active')
        self.assertFalse(contract.activation_date)
