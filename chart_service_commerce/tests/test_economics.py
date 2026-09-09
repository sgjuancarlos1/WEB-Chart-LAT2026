# -*- coding: utf-8 -*-
"""Economía del contrato (bloque 4, sin inventar condiciones comerciales).

Con fixtures sintéticos de precio/impuestos EXPLÍCITOS demuestra:
- El snapshot económico es inmutable tras la contratación (la edición de la
  línea origen NO altera el componente).
- La asociación por línea: implementación (único) + mensualidad (recurrente)
  generan componentes separados.
- Un cargo único se cobra UNA sola vez (ningún período recurrente).
- Los períodos se calculan desde el ancla (meses calendario).
- Los borradores de factura no se duplican (idempotentes).
"""
from datetime import date

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import ChartContractCommon


@tagged('post_install', '-at_install')
class TestEconomics(ChartContractCommon):

    _seq = 0

    def _contract(self, lines):
        type(self)._seq += 1
        n = type(self)._seq
        partner = self._portal_partner('Cliente Eco %d' % n, 'eco%d@test.com' % n)
        order = self._make_order(partner, lines)
        order.action_confirm()
        return order.chart_contract_ids

    def test_01_snapshot_immutable_after_origin_line_edit(self):
        """Editar la línea de pedido origen NO altera el snapshot congelado."""
        contract = self._contract([(self.test_service.product_variant_id, 1)])
        comp = contract.economic_component_ids[0]
        before = (comp.price_unit, comp.quantity, comp.price_total, comp.name)
        line = comp.order_line_id
        line.write({'price_unit': 999.0, 'product_uom_qty': 7})
        after = (comp.price_unit, comp.quantity, comp.price_total, comp.name)
        self.assertEqual(after, before)
        self.assertEqual(comp.price_unit, 1500.0)
        self.assertEqual(comp.quantity, 1.0)

    def test_02_snapshot_not_writable(self):
        """El snapshot no admite reescritura (inmutabilidad activa)."""
        contract = self._contract([(self.test_service.product_variant_id, 1)])
        comp = contract.economic_component_ids[0]
        with self.assertRaises(ValidationError):
            comp.write({'price_unit': 1.0})

    def test_03_implementation_plus_recurring_association(self):
        """Implementación (única) + mensualidad: componentes separados por línea."""
        contract = self._contract([
            (self.test_service_one_time.product_variant_id, 1),
            (self.test_service.product_variant_id, 1),
        ])
        comps = contract.economic_component_ids
        self.assertEqual(len(comps), 2)
        one_time = comps.filtered(lambda c: c.charge_type == 'one_time')
        recurring = comps.filtered(lambda c: c.charge_type == 'recurring')
        self.assertEqual(len(one_time), 1)
        self.assertEqual(len(recurring), 1)
        self.assertEqual(one_time.periodicity, 'none')
        self.assertEqual(recurring.periodicity, 'monthly')
        # cada componente conserva la variante de su propia línea
        self.assertEqual(one_time.product_id,
                         self.test_service_one_time.product_variant_id)
        self.assertEqual(recurring.product_id,
                         self.test_service.product_variant_id)

    def test_04_one_time_charged_exactly_once(self):
        """Un cargo único NO genera períodos recurrentes (se cobra una vez)."""
        contract = self._contract(
            [(self.test_service_one_time.product_variant_id, 1)])
        contract.write({
            'state': 'pending_activation_approval',
            'environment_checked': True,
            'environment_url': 'https://eco-ot.test.chart.lat',
            'email_verified': True,
        })
        contract.action_activate()
        contract.with_context(chart_allow_anchor_correction=True).write(
            {'billing_anchor_date': date(2026, 1, 31)})
        periods = self.env['chart.service.billing.period'].generate_periods(
            contract, count=6)
        self.assertFalse(periods)
        self.assertFalse(contract.billing_period_ids)
        recurring = contract.economic_component_ids.filtered(
            lambda c: c.charge_type == 'recurring')
        self.assertFalse(recurring)

    def test_05_periods_from_anchor_no_concurrent_draft_duplication(self):
        """Períodos desde el ancla y borradores sin duplicación (reintento)."""
        contract = self._contract([(self.test_service.product_variant_id, 1)])
        contract.write({
            'state': 'pending_activation_approval',
            'environment_checked': True,
            'environment_url': 'https://eco-m.test.chart.lat',
            'email_verified': True,
        })
        contract.action_activate()
        contract.with_context(chart_allow_anchor_correction=True).write(
            {'billing_anchor_date': date(2026, 1, 31)})
        Period = self.env['chart.service.billing.period']
        periods = Period.generate_periods(contract, count=2)
        self.assertEqual(len(periods), 2)
        self.assertEqual(periods[0].date_start, date(2026, 1, 31))
        # dos generaciones concurrentes del borrador: sigue habiendo 1 por período
        self.env['ir.config_parameter'].set_param(
            'chart_service_commerce.enable_draft_invoicing', 'True')
        try:
            periods.action_generate_draft_invoice()
            periods.action_generate_draft_invoice()
        finally:
            self.env['ir.config_parameter'].set_param(
                'chart_service_commerce.enable_draft_invoicing', 'False')
        self.assertEqual(len(periods.mapped('move_id')), 2)
        for move in periods.mapped('move_id'):
            self.assertEqual(move.state, 'draft')
            self.assertFalse(move.payment_state in ('paid', 'in_payment'))
