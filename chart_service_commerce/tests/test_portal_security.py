# -*- coding: utf-8 -*-
"""Pruebas de seguridad del portal: cliente A no ve el contrato de cliente B."""
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import ChartContractCommon


@tagged('post_install', '-at_install')
class TestPortalSecurity(ChartContractCommon):

    def test_01_client_a_cannot_read_client_b_contract(self):
        """La ACL de portal + regla de propiedad impiden leer el contrato ajeno."""
        partner_a = self._portal_partner('Cliente A', 'a@sec.com')
        partner_b = self._portal_partner('Cliente B', 'b@sec.com')
        order_b = self._make_order(partner_b, [(self.test_service.product_variant_id, 1)])
        order_b.action_confirm()
        contract_b = order_b.chart_contract_ids

        user_a = partner_a.user_ids[0]
        # lectura directa como A debe fallar (sin sudo)
        with self.assertRaises(AccessError):
            contract_b.with_user(user_a).read(['name'])
        # búsqueda como A no devuelve el contrato de B
        found = self.env['chart.service.contract'].with_user(user_a).search(
            [('id', '=', contract_b.id)])
        self.assertFalse(found)

    def test_02_client_a_can_read_own_contract(self):
        partner_a = self._portal_partner('Cliente A', 'a2@sec.com')
        order_a = self._make_order(partner_a, [(self.test_service.product_variant_id, 1)])
        order_a.action_confirm()
        contract_a = order_a.chart_contract_ids
        user_a = partner_a.user_ids[0]
        found = self.env['chart.service.contract'].with_user(user_a).search(
            [('id', '=', contract_a.id)])
        self.assertEqual(found, contract_a)

    def test_03_portal_cannot_write_contract(self):
        """El portal no puede modificar ni activar contratos."""
        partner_a = self._portal_partner('Cliente A', 'a3@sec.com')
        order_a = self._make_order(partner_a, [(self.test_service.product_variant_id, 1)])
        order_a.action_confirm()
        contract_a = order_a.chart_contract_ids
        user_a = partner_a.user_ids[0]
        with self.assertRaises(AccessError):
            contract_a.with_user(user_a).write({'state': 'active'})

    def _company_with_contacts(self, company_name, emails):
        """Crea una empresa con N contactos (cada uno con usuario portal)."""
        company = self.env['res.partner'].create({'name': company_name, 'is_company': True})
        contacts = []
        for email in emails:
            contact = self.env['res.partner'].create({
                'name': email.split('@')[0], 'email': email,
                'parent_id': company.id,
            })
            user = self.env['res.users'].create({
                'name': contact.name, 'login': email, 'email': email,
                'partner_id': contact.id,
                'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])],
            })
            contacts.append(contact)
        return company, contacts

    def test_04_same_company_contacts_isolated_without_authorization(self):
        """Dos contactos de la MISMA empresa NO se ven los contratos entre sí
        sin autorización explícita (antes child_of lo permitía)."""
        company, (c1, c2) = self._company_with_contacts('Empresa X', ['x1@sec.com', 'x2@sec.com'])
        # c1 contrata un servicio
        order = self._make_order(c1, [(self.test_service.product_variant_id, 1)])
        order.action_confirm()
        contract = order.chart_contract_ids
        self.assertEqual(contract.partner_id, c1)
        # ambos comparten el mismo commercial_partner_id (la empresa)
        self.assertEqual(c1.commercial_partner_id, company)
        self.assertEqual(c2.commercial_partner_id, company)
        # c2 NO debe ver el contrato de c1 (misma empresa, sin autorización)
        user_c2 = c2.user_ids[0]
        with self.assertRaises(AccessError):
            contract.with_user(user_c2).read(['name'])
        found = self.env['chart.service.contract'].with_user(user_c2).search(
            [('id', '=', contract.id)])
        self.assertFalse(found)
        # c1 sí ve su propio contrato
        user_c1 = c1.user_ids[0]
        found = self.env['chart.service.contract'].with_user(user_c1).search(
            [('id', '=', contract.id)])
        self.assertEqual(found, contract)

    def test_05_explicitly_authorized_member_can_read(self):
        """Un miembro EXPLÍCITAMENTE autorizado sí puede leer el contrato."""
        company, (c1, c2) = self._company_with_contacts('Empresa Y', ['y1@sec.com', 'y2@sec.com'])
        order = self._make_order(c1, [(self.test_service.product_variant_id, 1)])
        order.action_confirm()
        contract = order.chart_contract_ids
        user_c2 = c2.user_ids[0]
        # sin autorización: no lo ve
        self.assertFalse(self.env['chart.service.contract'].with_user(user_c2).search(
            [('id', '=', contract.id)]))
        # autorización explícita del titular
        contract.write({'authorized_partner_ids': [(4, c2.id)]})
        found = self.env['chart.service.contract'].with_user(user_c2).search(
            [('id', '=', contract.id)])
        self.assertEqual(found, contract)

    def test_06_tasks_and_periods_isolated_for_other_company_contact(self):
        """Tareas y períodos del contrato también quedan aislados para un
        contacto de la misma empresa sin autorización."""
        company, (c1, c2) = self._company_with_contacts('Empresa Z', ['z1@sec.com', 'z2@sec.com'])
        order = self._make_order(c1, [(self.test_service.product_variant_id, 1)])
        order.action_confirm()
        contract = order.chart_contract_ids
        # preparación crea tareas reales
        contract.action_start_preparation()
        self.assertTrue(contract.task_ids)
        user_c2 = c2.user_ids[0]
        # c2 no ve las tareas del contrato de c1
        task = contract.task_ids[0]
        with self.assertRaises(AccessError):
            task.with_user(user_c2).read(['name'])
        found_tasks = self.env['chart.provisioning.task'].with_user(user_c2).search(
            [('id', 'in', contract.task_ids.ids)])
        self.assertFalse(found_tasks)
        # c2 no ve los períodos (si existieran) del contrato de c1
        found_periods = self.env['chart.service.billing.period'].with_user(user_c2).search(
            [('contract_id', '=', contract.id)])
        self.assertFalse(found_periods)
