# -*- coding: utf-8 -*-
"""Pruebas del flujo de contratación (escenarios E del prompt)."""
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import ChartContractCommon


@tagged('post_install', '-at_install')
class TestContractFlow(ChartContractCommon):

    def test_01_confirm_creates_contract_pending_preparation(self):
        """Confirmar un pedido con servicio crea el contrato en 'Pendiente de
        preparación', sin activar ni facturar."""
        partner = self._portal_partner('Cliente A', 'a@test.com')
        order = self._make_order(partner, [(self.test_service.product_variant_id, 1)])
        order.action_confirm()
        self.assertEqual(order.state, 'sale')
        self.assertEqual(order.chart_contract_count, 1)
        contract = order.chart_contract_ids
        self.assertEqual(contract.state, 'pending_preparation')
        self.assertEqual(contract.payment_status, 'not_required')
        self.assertFalse(contract.activation_date)
        self.assertFalse(contract.billing_period_ids)
        self.assertEqual(contract.amount_total, 1500.0)
        self.assertEqual(contract.periodicity, 'monthly')
        self.assertEqual(contract.service_type, 'subscription')

    def test_02_double_confirm_does_not_duplicate(self):
        """Doble clic / reintento no duplica la contratación.

        El flujo real pasa por el controller /shop/chart/confirm, que verifica
        state != 'draft' y redirige sin volver a confirmar. A nivel de modelo,
        Odoo nativo impide confirmar dos veces el mismo pedido, por lo que un
        segundo action_confirm no puede duplicar contratos.
        """
        partner = self._portal_partner('Cliente B', 'b@test.com')
        order = self._make_order(partner, [(self.test_service.product_variant_id, 1)])
        order.action_confirm()
        first = order.chart_contract_ids
        self.assertEqual(order.chart_contract_count, 1)
        # segundo intento de confirmar: Odoo nativo lo rechaza (no duplica)
        with self.assertRaises(UserError):
            order.action_confirm()
        self.assertEqual(order.chart_contract_count, 1)
        self.assertEqual(order.chart_contract_ids, first)

    def test_03_multiple_services_independent_contracts(self):
        """Varios servicios en un pedido -> contratos independientes."""
        partner = self._portal_partner('Cliente C', 'c@test.com')
        order = self._make_order(partner, [
            (self.test_service.product_variant_id, 1),
            (self.test_service_one_time.product_variant_id, 1),
        ])
        order.action_confirm()
        self.assertEqual(order.chart_contract_count, 2)
        recurring = order.chart_contract_ids.filtered(lambda c: c.periodicity == 'monthly')
        one_time = order.chart_contract_ids.filtered(lambda c: c.periodicity == 'none')
        self.assertEqual(len(recurring), 1)
        self.assertEqual(len(one_time), 1)
        self.assertEqual(one_time.service_type, 'one_time')
        self.assertEqual(one_time.amount_total, 5000.0)

    def test_04_variant_preserved_in_contract(self):
        """La variante (product.product) se conserva en la línea del contrato."""
        partner = self._portal_partner('Cliente D', 'd@test.com')
        order = self._make_order(partner, [(self.variant_mensual, 2)])
        order.action_confirm()
        contract = order.chart_contract_ids
        self.assertEqual(contract.product_id, self.variant_mensual)
        self.assertEqual(contract.order_line_ids.product_id, self.variant_mensual)
        self.assertEqual(contract.order_line_ids.product_uom_qty, 2.0)
        self.assertEqual(contract.amount_total, 1800.0)

    def test_05_amounts_validated_on_server(self):
        """Los importes del contrato provienen del pedido (no del catálogo)."""
        partner = self._portal_partner('Cliente E', 'e@test.com')
        order = self._make_order(partner, [(self.test_service.product_variant_id, 3)])
        order.action_confirm()
        contract = order.chart_contract_ids
        self.assertEqual(contract.amount_total, 4500.0)
        # cambiar el precio del catálogo NO altera el contrato ya creado
        self.test_service.list_price = 99999.0
        self.assertEqual(contract.amount_total, 4500.0)

    def test_06_activation_requires_full_preparation(self):
        """No se puede activar sin preparación completa y comprobación de entorno."""
        partner = self._portal_partner('Cliente F', 'f@test.com')
        order = self._make_order(partner, [(self.test_service.product_variant_id, 1)])
        order.action_confirm()
        contract = order.chart_contract_ids
        # no se puede activar desde 'pending_preparation'
        with self.assertRaises(UserError):
            contract.action_activate()
        # iniciar preparación crea tareas reales (no simuladas)
        contract.action_start_preparation()
        self.assertEqual(contract.state, 'in_preparation')
        self.assertEqual(contract.task_total, 5)
        self.assertEqual(contract.task_done, 0)
        # no se puede marcar entorno comprobado con tareas abiertas
        with self.assertRaises(UserError):
            contract.action_mark_environment_checked()
        # cerrar tareas exige evidencia
        task = contract.task_ids[0]
        with self.assertRaises(UserError):
            task.action_done()
        task.completion_note = 'Verificado por test'
        task.action_done()
        self.assertEqual(task.state, 'done')
        self.assertTrue(task.done_by_id)
        self.assertTrue(task.done_date)
        # cerrar el resto
        for t in contract.task_ids[1:]:
            t.completion_note = 'Verificado por test'
            t.action_done()
        # sin URL de entorno no se puede registrar la comprobación
        with self.assertRaises(UserError):
            contract.action_mark_environment_checked()
        contract.action_mark_environment_checked(
            environment_url='https://cliente-f.test.chart.lat')
        self.assertEqual(contract.state, 'pending_activation_approval')
        self.assertTrue(contract.environment_checked)
        self.assertTrue(contract.environment_checked_date)
        self.assertTrue(contract.environment_checked_by)
        self.assertEqual(contract.environment_url, 'https://cliente-f.test.chart.lat')
        self.assertTrue(contract.ready_at)
        # activación real registra ancla
        contract.email_verified = True
        contract.action_activate()
        self.assertEqual(contract.state, 'active')
        self.assertTrue(contract.activation_date)
        self.assertTrue(contract.billing_anchor_date)
        # una vez activo, no se puede volver a borrador ni cambiar el ancla
        with self.assertRaises(UserError):
            contract.action_reset_draft()
        with self.assertRaises(UserError):
            contract.write({'billing_anchor_date': contract.billing_anchor_date})
        # corrección explícita y trazada sí se permite
        contract.with_context(chart_allow_anchor_correction=True).write(
            {'billing_anchor_date': contract.billing_anchor_date})

    def test_07_terms_persisted(self):
        """La aceptación de condiciones queda persistente en el pedido y contrato."""
        partner = self._portal_partner('Cliente G', 'g@test.com')
        order = self._make_order(partner, [(self.test_service.product_variant_id, 1)])
        order.chart_accept_terms()
        self.assertTrue(order.chart_terms_accepted)
        self.assertTrue(order.chart_terms_version)
        self.assertTrue(order.chart_terms_accepted_datetime)
        order.action_confirm()
        contract = order.chart_contract_ids
        self.assertTrue(contract.terms_accepted)
        self.assertEqual(contract.terms_version, order.chart_terms_version)
        self.assertTrue(contract.economic_terms_snapshot)
        # el snapshot congela el monto del pedido (formato de moneda local)
        self.assertIn(self.test_service.display_name, contract.economic_terms_snapshot)
        self.assertIn(order.currency_id.format(1500.0), contract.economic_terms_snapshot)

    def test_08_contract_reappears_after_relogin(self):
        """El contrato persiste y reaparece tras cerrar sesión e ingresar."""
        partner = self._portal_partner('Cliente H', 'h@test.com')
        order = self._make_order(partner, [(self.test_service.product_variant_id, 1)])
        order.action_confirm()
        contract = order.chart_contract_ids
        # simula una nueva sesión: se re-lee el registro desde el modelo (BD)
        reloaded = self.env['chart.service.contract'].browse(contract.id)
        self.assertTrue(reloaded.exists())
        self.assertEqual(reloaded.state, 'pending_preparation')
        self.assertEqual(reloaded.partner_id, partner)

    def test_09_structured_economic_components(self):
        """Cada contrato guarda componentes económicos ESTRUCTURADOS por línea.

        Un servicio mensual genera un componente recurrente; un cargo único
        genera un componente único. Se conservan variante, cantidad, precio,
        moneda e impuestos congelados del pedido.
        """
        partner = self._portal_partner('Cliente I', 'i@test.com')
        # servicio mensual (recurrente)
        order = self._make_order(partner, [(self.test_service.product_variant_id, 2)])
        order.action_confirm()
        contract = order.chart_contract_ids
        self.assertTrue(contract.economic_component_ids)
        comp = contract.economic_component_ids[0]
        self.assertEqual(comp.charge_type, 'recurring')
        self.assertEqual(comp.periodicity, 'monthly')
        self.assertEqual(comp.order_line_id, contract.order_line_ids)
        self.assertEqual(comp.product_id, self.test_service.product_variant_id)
        self.assertEqual(comp.quantity, 2)
        self.assertEqual(comp.price_unit, 1500.0)
        self.assertEqual(comp.price_total, 3000.0)
        self.assertEqual(comp.currency_id, contract.currency_id)
        self.assertEqual(comp.terms_version, contract.terms_version)
        # cargo único
        order2 = self._make_order(partner, [(self.test_service_one_time.product_variant_id, 1)])
        order2.action_confirm()
        contract2 = order2.chart_contract_ids
        comp2 = contract2.economic_component_ids[0]
        self.assertEqual(comp2.charge_type, 'one_time')
        self.assertEqual(comp2.periodicity, 'none')
        self.assertEqual(comp2.price_total, 5000.0)
        # los importes del contrato se derivan de los componentes estructurados
        self.assertEqual(contract.amount_recurring, 3000.0)
        self.assertEqual(contract.amount_one_time, 0.0)
        self.assertEqual(contract2.amount_one_time, 5000.0)
        self.assertEqual(contract2.amount_recurring, 0.0)
