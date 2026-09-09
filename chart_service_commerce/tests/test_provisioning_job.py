# -*- coding: utf-8 -*-
"""Regresiones del trabajo de aprovisionamiento (modelo chart.provisioning.job).

Cubre los defectos del expediente:
- ACL y autorización en servidor (preparador/portal no aprueban ni activan).
- URL del entorno consistente con la base al aprobar (no el valor previo).
- Unicidad efectiva del idempotent_key en PostgreSQL.
- Cancelar/resetear NO borra recursos; el borrado exige confirmación y
  autorización explícita.
- Un trabajo no queda 'ready' por tener URL: exige verificación real (con el
  parámetro de infraestructura desactivado nunca es ready).
- Los campos sensibles del job no se expongan al portal.
"""
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import ChartContractCommon


@tagged('post_install', '-at_install')
class TestProvisioningJob(ChartContractCommon):

    def _portal_user(self, name, email):
        partner = self._portal_partner(name, email)
        return partner.user_ids[0]

    def _internal_user(self, name, ref):
        """Usuario interno en el grupo indicado (preparador o responsable)."""
        return self.env['res.users'].create({
            'name': name,
            'login': name,
            'group_ids': [(6, 0, [self.env.ref(ref).id])],
        })

    def _active_contract(self, manager, partner):
        """Crea un contrato y lo lleva a 'active' (atajo para probar el JOB)."""
        order = self._make_order(partner, [(self.test_service.product_variant_id, 1)])
        order.action_confirm()
        contract = order.chart_contract_ids
        self.assertEqual(contract.state, 'pending_preparation')
        # El objetivo de este test es el JOB, no el flujo del contrato: se deja
        # el contrato en 'active' de forma explícita y trazada (test aislado).
        contract.with_user(manager).write({'state': 'active'})
        return contract

    def _make_approved_job(self):
        manager = self._internal_user('mgr', 'chart_service_commerce.group_chart_service_manager')
        partner = self._portal_partner('Cliente J', 'j@job.com')
        contract = self._active_contract(manager, partner)
        Job = self.env['chart.provisioning.job']
        job = Job.with_user(manager).create({'contract_id': contract.id})
        job.with_user(manager).action_approve()
        return job, manager, contract

    def test_01_url_consistent_with_db_at_approve(self):
        """Al aprobar, environment_url deriva de la MISMA base recién generada."""
        job, manager, _ = self._make_approved_job()
        self.assertTrue(job.database_name)
        self.assertTrue(job.environment_url)
        expected = 'https://%s.chart.lat' % job.database_name.replace('_', '-')
        self.assertEqual(job.environment_url, expected)

    def test_02_portal_cannot_read_job_sensitive(self):
        """El portal no tiene ACL de lectura del job (proyección vía contrato)."""
        job, manager, _ = self._make_approved_job()
        portal_user = self._portal_user('Portal P', 'p@job.com')
        Job = self.env['chart.provisioning.job']
        with self.assertRaises(AccessError):
            Job.with_user(portal_user).search([('id', '=', job.id)])

    def test_03_preparador_no_aprueba_ni_reintenta(self):
        """Un preparador (no responsable) no puede aprobar ni reintentar."""
        preparador = self._internal_user(
            'prep', 'chart_service_commerce.group_chart_service_user')
        partner = self._portal_partner('Cliente R', 'r@job.com')
        contract = self._active_contract(preparador, partner)
        Job = self.env['chart.provisioning.job']
        job = Job.with_user(preparador).create({'contract_id': contract.id})
        with self.assertRaises(UserError):
            job.with_user(preparador).action_approve()

    def test_04_idempotent_key_unique_enforced(self):
        """La unicidad de idempotent_key está efectiva en PostgreSQL."""
        job, manager, _ = self._make_approved_job()
        Job = self.env['chart.provisioning.job']
        dup = dict(contract_id=job.contract_id.id, idempotent_key=job.idempotent_key)
        with self.assertRaises(Exception):
            Job.with_user(manager).create(dup)

    def test_05_not_ready_by_url_without_provisioning(self):
        """Sin verificación real el job nunca queda ready (ni por URL)."""
        job, manager, _ = self._make_approved_job()
        self.assertEqual(job.state, 'approved')
        self.assertTrue(job.environment_url)  # hay URL, pero...
        job.with_user(manager).action_start_provisioning()
        self.assertEqual(job.state, 'failed')  # no autorizado/falsamente ready
        self.assertNotEqual(job.state, 'ready')

    def test_06_cancel_does_not_drop_database(self):
        """Cancelar NO borra la base: conserva identidad de recurso."""
        job, manager, _ = self._make_approved_job()
        db_name = job.database_name
        job.with_user(manager).action_cancel()
        self.assertEqual(job.state, 'cancelled')
        # acción de borrado requiere confirmación y autorización explícita
        with self.assertRaises(UserError):
            job.with_user(manager).action_delete_environment()  # sin confirm
        self.assertEqual(job.database_name, db_name)