# -*- coding: utf-8 -*-
"""Pruebas de períodos facturables: meses calendario desde el ancla."""
from datetime import date, timedelta

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ChartContractCommon


@tagged('post_install', '-at_install')
class TestBillingPeriods(ChartContractCommon):

    _partner_seq = 0

    def _active_contract(self, periodicity, anchor=date(2026, 1, 31)):
        type(self)._partner_seq += 1
        email = 'p%d@test.com' % type(self)._partner_seq
        partner = self._portal_partner('Cliente P%d' % type(self)._partner_seq, email)
        product = self.test_service if periodicity == 'monthly' else self.test_service_one_time
        order = self._make_order(partner, [(product.product_variant_id, 1)])
        order.action_confirm()
        contract = order.chart_contract_ids
        # activar manualmente (action_activate fija el ancla a la fecha de hoy)
        contract.write({
            'state': 'pending_activation_approval',
            'environment_checked': True,
            'environment_url': 'https://cliente-p.test.chart.lat',
            'email_verified': True,
        })
        contract.action_activate()
        # fijar un ancla determinista para probar el cálculo de períodos
        # (corrección explícita y trazada, permitida por el modelo)
        contract.with_context(chart_allow_anchor_correction=True).write(
            {'billing_anchor_date': anchor})
        return contract

    def _expected(self, anchor, k, months_per_cycle=1):
        """Frontera B(k) esperada según la política (independiente del código)."""
        from dateutil.relativedelta import relativedelta
        return anchor + relativedelta(months=k * months_per_cycle)

    def test_01_monthly_calendar_months_from_anchor(self):
        """Períodos mensuales contiguos desde el ancla (sin huecos ni solapes)."""
        contract = self._active_contract('monthly')
        periods = self.env['chart.service.billing.period'].generate_periods(contract, count=3)
        self.assertEqual(len(periods), 3)
        starts = periods.mapped('date_start')
        ends = periods.mapped('date_end')
        # el ancla fija el inicio del primer período
        self.assertEqual(starts[0], date(2026, 1, 31))
        # cada período termina el día anterior al inicio del siguiente (contiguo)
        self.assertEqual(ends[0], starts[1] - timedelta(days=1))
        self.assertEqual(ends[1], starts[2] - timedelta(days=1))
        # el fin nunca es anterior al inicio
        for s, e in zip(starts, ends):
            self.assertGreaterEqual(e, s)
        # cada frontera coincide con la política independiente del algoritmo
        for k in range(1, 4):
            self.assertEqual(starts[k - 1], self._expected(date(2026, 1, 31), k - 1))
            self.assertEqual(ends[k - 1], self._expected(date(2026, 1, 31), k) - timedelta(days=1))

    def test_02_no_duplicates_on_regeneration(self):
        """Regenerar no crea duplicados (idempotente)."""
        contract = self._active_contract('monthly')
        self.env['chart.service.billing.period'].generate_periods(contract, count=3)
        self.env['chart.service.billing.period'].generate_periods(contract, count=3)
        self.assertEqual(len(contract.billing_period_ids), 3)

    def test_03_no_periods_without_anchor(self):
        """Sin ancla de activación no se generan períodos (no se inventa fecha)."""
        partner = self._portal_partner('Cliente Q', 'q@test.com')
        order = self._make_order(partner, [(self.test_service.product_variant_id, 1)])
        order.action_confirm()
        contract = order.chart_contract_ids
        self.assertFalse(contract.billing_anchor_date)
        periods = self.env['chart.service.billing.period'].generate_periods(contract, count=3)
        self.assertEqual(len(periods), 0)

    def test_04_one_time_has_no_periods(self):
        """Un cargo único no genera períodos recurrentes."""
        contract = self._active_contract('none')
        periods = self.env['chart.service.billing.period'].generate_periods(contract, count=3)
        self.assertEqual(len(periods), 0)

    def test_05_no_invoice_emitted(self):
        """Esta entrega NO emite facturas: move_id siempre False."""
        contract = self._active_contract('monthly')
        periods = self.env['chart.service.billing.period'].generate_periods(contract, count=2)
        self.assertTrue(all(not p.move_id for p in periods))
        self.assertTrue(all(not p.invoiced for p in periods))

    def test_06_anchor_31_january_no_drift(self):
        """Ancla 31 ene 2027: NO debe desplazarse a 28 feb → 28 mar.

        Esperado (política): [31 ene, 27 feb] → [28 feb, 30 mar] → [31 mar, 29 abr].
        """
        contract = self._active_contract('monthly', anchor=date(2027, 1, 31))
        periods = self.env['chart.service.billing.period'].generate_periods(contract, count=3)
        self.assertEqual(len(periods), 3)
        starts = periods.mapped('date_start')
        ends = periods.mapped('date_end')
        self.assertEqual(starts[0], date(2027, 1, 31))
        self.assertEqual(ends[0], date(2027, 2, 27))   # B(1)-1
        self.assertEqual(starts[1], date(2027, 2, 28))  # B(1)
        self.assertEqual(ends[1], date(2027, 3, 30))   # B(2)-1
        self.assertEqual(starts[2], date(2027, 3, 31))  # B(2)
        self.assertEqual(ends[2], date(2027, 4, 29))   # B(3)-1
        # cada frontera coincide con la política independiente
        for k in range(1, 4):
            self.assertEqual(starts[k - 1], self._expected(date(2027, 1, 31), k - 1))
            self.assertEqual(ends[k - 1], self._expected(date(2027, 1, 31), k) - timedelta(days=1))

    def test_07_anchor_days_28_29_30_31(self):
        """Anclas en días 28, 29, 30 y 31 conservan el día del ancla en cada ciclo."""
        for day in (28, 29, 30, 31):
            anchor = date(2026, 1, day)
            contract = self._active_contract('monthly', anchor=anchor)
            periods = self.env['chart.service.billing.period'].generate_periods(contract, count=3)
            starts = periods.mapped('date_start')
            # el día del ancla se conserva en cada inicio de período
            self.assertEqual(starts[0], anchor)
            for k in range(1, 3):
                self.assertEqual(starts[k], self._expected(anchor, k))
            # contiguos, sin huecos ni solapes
            ends = periods.mapped('date_end')
            for k in range(1, 3):
                self.assertEqual(ends[k - 1], starts[k] - timedelta(days=1))

    def test_08_leap_year_february(self):
        """Febrero bisiesto: ancla 31 ene 2028 (año bisiesto) → feb 29."""
        anchor = date(2028, 1, 31)
        contract = self._active_contract('monthly', anchor=anchor)
        periods = self.env['chart.service.billing.period'].generate_periods(contract, count=2)
        starts = periods.mapped('date_start')
        ends = periods.mapped('date_end')
        self.assertEqual(starts[0], date(2028, 1, 31))
        self.assertEqual(ends[0], date(2028, 2, 28))   # B(1)=29 feb, -1 día
        self.assertEqual(starts[1], date(2028, 2, 29))  # B(1) conserva el 29
        self.assertEqual(ends[1], date(2028, 3, 30))   # B(2)=31 mar, -1 día

    def test_09_twelve_cycles_no_drift(self):
        """Doce ciclos mensuales desde un ancla en día 31 no pierden el ancla."""
        anchor = date(2026, 1, 31)
        contract = self._active_contract('monthly', anchor=anchor)
        periods = self.env['chart.service.billing.period'].generate_periods(contract, count=12)
        self.assertEqual(len(periods), 12)
        starts = periods.mapped('date_start')
        ends = periods.mapped('date_end')
        for k in range(1, 13):
            self.assertEqual(starts[k - 1], self._expected(anchor, k - 1))
            self.assertEqual(ends[k - 1], self._expected(anchor, k) - timedelta(days=1))
        # sin huecos ni solapes en toda la serie
        for k in range(1, 12):
            self.assertEqual(ends[k - 1], starts[k] - timedelta(days=1))

    def test_10_partial_regeneration_keeps_anchor(self):
        """Regenerar solo períodos nuevos no altera los ya existentes."""
        anchor = date(2026, 1, 31)
        contract = self._active_contract('monthly', anchor=anchor)
        self.env['chart.service.billing.period'].generate_periods(contract, count=2)
        # regenerar con count=4: solo añade los índices 3 y 4
        self.env['chart.service.billing.period'].generate_periods(contract, count=4)
        self.assertEqual(len(contract.billing_period_ids), 4)
        # los dos primeros no cambiaron
        p1 = contract.billing_period_ids.filtered(lambda p: p.index == 1)
        p2 = contract.billing_period_ids.filtered(lambda p: p.index == 2)
        self.assertEqual(p1.date_start, date(2026, 1, 31))
        self.assertEqual(p1.date_end, date(2026, 2, 27))
        self.assertEqual(p2.date_start, date(2026, 2, 28))
        self.assertEqual(p2.date_end, date(2026, 3, 30))
        # los nuevos siguen la política desde el ancla
        p3 = contract.billing_period_ids.filtered(lambda p: p.index == 3)
        p4 = contract.billing_period_ids.filtered(lambda p: p.index == 4)
        self.assertEqual(p3.date_start, date(2026, 3, 31))
        self.assertEqual(p4.date_start, date(2026, 4, 30))

    def test_11_draft_invoice_disabled_by_default(self):
        """La generación de borradores está deshabilitada por defecto."""
        contract = self._active_contract('monthly')
        periods = self.env['chart.service.billing.period'].generate_periods(contract, count=2)
        with self.assertRaises(UserError):
            periods.action_generate_draft_invoice()

    def test_12_draft_invoice_generation(self):
        """Con la config habilitada se generan borradores (no timbrados ni pagados)."""
        self.env['ir.config_parameter'].set_param(
            'chart_service_commerce.enable_draft_invoicing', 'True')
        contract = self._active_contract('monthly')
        periods = self.env['chart.service.billing.period'].generate_periods(contract, count=2)
        periods.action_generate_draft_invoice()
        for period in periods:
            self.assertTrue(period.move_id)
            self.assertTrue(period.draft_invoice_generated)
            move = period.move_id
            self.assertEqual(move.move_type, 'out_invoice')
            self.assertEqual(move.state, 'draft')   # NO publicado
            self.assertFalse(move.payment_state in ('paid', 'in_payment'))
            self.assertEqual(move.partner_id, contract.partner_id)
            self.assertEqual(move.amount_total, period.amount)
        # idempotente: no crea duplicados
        periods.action_generate_draft_invoice()
        self.assertEqual(len(periods.move_id), 2)
        self.env['ir.config_parameter'].set_param(
            'chart_service_commerce.enable_draft_invoicing', 'False')
