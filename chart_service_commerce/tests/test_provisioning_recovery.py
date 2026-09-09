# -*- coding: utf-8 -*-
"""El aprovisionamiento es RECUPERABLE (bloque 3 del expediente).

Demuestra con fixtures sintéticos (sin crear infraestructura real):
- La misma operación conserva su identidad en todos sus reintentos.
- Dos ejecutores concurrentes no crean dos recursos (unicidad real en BD).
- El fallo persiste incluso cuando la conexión SQL entra en error.
- Una caída después de crear la base permite reconciliar sin duplicar.
- Contrato, tareas y job quedan vinculados coherentemente sin exigir entorno.
- Cancelar o resetear no elimina recursos ni pierde su identidad.
"""
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ChartContractCommon


@tagged('post_install', '-at_install')
class TestProvisioningRecovery(ChartContractCommon):

    _seq = 0

    def _manager(self):
        type(self)._seq += 1
        n = type(self)._seq
        return self.env['res.users'].create({
            'name': 'Responsable Rec %d' % n,
            'login': 'mgr-rec-%d@test.com' % n,
            'email': 'mgr-rec-%d@test.com' % n,
            'group_ids': [(6, 0, [
                self.env.ref('chart_service_commerce.group_chart_service_manager').id])],
        })

    def _approved_job(self):
        manager = self._manager()
        type(self)._seq += 1
        n = type(self)._seq
        partner = self._portal_partner('Cliente Rec %d' % n, 'rec%d@test.com' % n)
        order = self._make_order(partner, [(self.test_service.product_variant_id, 1)])
        order.action_confirm()
        contract = order.chart_contract_ids
        # el JOB es el objetivo del test: contrato activo explícito (el
        # responsable puede activarlo por escritura directa autorizada)
        contract.with_user(manager).write({'state': 'active'})
        job = self.env['chart.provisioning.job'].with_user(manager).create(
            {'contract_id': contract.id})
        job.with_user(manager).action_approve()
        return job, manager, contract

    def _patch_infra(self, create_counter=None, db_exists=None, verify=None):
        """Parchea los adaptadores de infraestructura (NADA toca PostgreSQL real)."""
        patches = [
            patch.object(type(self.env['chart.provisioning.job']),
                         '_install_modules_in_database', lambda self, db: None),
            patch.object(type(self.env['chart.provisioning.job']),
                         '_create_filestore', lambda self, db: None),
            patch.object(type(self.env['chart.provisioning.job']),
                         '_post_provisioning_setup', lambda self: None),
        ]
        if create_counter is not None:
            def fake_create(job_self, db_name):
                # réplica del contrato de _create_database: solo crea si el
                # recurso NO existe (reconciliación idempotente)
                create_counter['attempts'] += 1
                if not db_exists(job_self, db_name):
                    create_counter['creations'] += 1
            patches.append(patch.object(
                type(self.env['chart.provisioning.job']),
                '_create_database', fake_create))
        if db_exists is not None:
            patches.append(patch.object(
                type(self.env['chart.provisioning.job']),
                '_db_exists', lambda job_self, db: db_exists(job_self, db)))
        if verify is not None:
            patches.append(patch.object(
                type(self.env['chart.provisioning.job']),
                '_verify_environment', lambda job_self: verify(job_self)))
        return patches

    def test_01_identity_preserved_across_retries(self):
        """La misma operación conserva identidad (clave, base y URL) en reintentos."""
        job, manager, _ = self._approved_job()
        key = job.idempotent_key
        db = job.database_name
        url = job.environment_url
        # intento 1: fallo de verificación (infra no autorizada)
        job.with_user(manager).action_start_provisioning()
        self.assertEqual(job.state, 'failed')
        # reintento: la infraestructura pasa (parcheada, sin recursos reales)
        patches = self._patch_infra(db_exists=lambda j, d: True,
                                    verify=lambda j: (True, ''))
        for p in patches:
            p.start()
        try:
            # se autoriza la infraestructura SOLO para este intento parcheado
            self.env['ir.config_parameter'].sudo().set_param(
                'chart_service_commerce.provisioning_enabled', 'True')
            job.with_user(manager).action_retry()
            job.with_user(manager).action_start_provisioning()
        finally:
            self.env['ir.config_parameter'].sudo().set_param(
                'chart_service_commerce.provisioning_enabled', 'False')
            for p in patches:
                p.stop()
        self.assertEqual(job.state, 'ready')
        self.assertEqual(job.idempotent_key, key)
        self.assertEqual(job.database_name, db)
        self.assertEqual(job.environment_url, url)

    def test_02_concurrent_executors_do_not_create_two_resources(self):
        """Dos ejecutores con la misma identidad: PostgreSQL impide duplicar."""
        job, manager, contract = self._approved_job()
        Job = self.env['chart.provisioning.job']
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                Job.sudo().create({'contract_id': contract.id,
                                   'idempotent_key': job.idempotent_key})
        self.assertEqual(Job.search_count(
            [('idempotent_key', '=', job.idempotent_key)]), 1)
        # y solo hay una identidad de recurso asignada
        self.assertEqual(len(job.database_name and [job.database_name] or []), 1)

    def test_03_failure_persists_when_sql_connection_in_error(self):
        """El fallo persiste aunque la conexión SQL entre en estado de error.

        No basta con omitir el raise: la frontera transaccional (savepoint)
        recupera la conexión y persiste ``failed`` en una transacción sana.
        """
        job, manager, _ = self._approved_job()

        def sql_boom(job_self):
            # deja la conexión en estado de error (aborta la transacción SQL)
            job_self.env.cr.execute("SELECT 1/0")

        with patch.object(type(job), '_provision_environment', sql_boom):
            res = job.with_user(manager).action_start_provisioning()
        self.assertFalse(res)
        self.assertEqual(job.state, 'failed')
        self.assertTrue(job.error_message)
        # lectura fresca: el fallo está persistido, no solo en caché
        fresh = self.env['chart.provisioning.job'].browse(job.id)
        self.assertEqual(fresh.state, 'failed')
        self.assertTrue(fresh.error_message)

    def test_04_crash_after_db_creation_reconciles_without_duplication(self):
        """Caída tras crear la base: el reintento reconcilia SIN duplicar."""
        job, manager, _ = self._approved_job()
        counter = {'attempts': 0, 'creations': 0}

        def db_exists(job_self, db):
            # tras la "creación" (DDL autocommit fuera de la transacción),
            # el recurso existe aunque el job haya fallado
            return counter['creations'] > 0

        def verify_ok(job_self):
            # solo el 2º intento pasa la verificación
            return (counter['attempts'] >= 2, '')

        patches = self._patch_infra(create_counter=counter,
                                    db_exists=db_exists, verify=verify_ok)
        for p in patches:
            p.start()
        try:
            # infraestructura autorizada SOLO para este escenario parcheado
            self.env['ir.config_parameter'].sudo().set_param(
                'chart_service_commerce.provisioning_enabled', 'True')
            job.with_user(manager).action_start_provisioning()   # intento 1: cae tras crear
            self.assertEqual(job.state, 'failed')
            job.with_user(manager).action_retry()
            job.with_user(manager).action_start_provisioning()   # intento 2: reconcilia
        finally:
            self.env['ir.config_parameter'].sudo().set_param(
                'chart_service_commerce.provisioning_enabled', 'False')
            for p in patches:
                p.stop()
        self.assertEqual(job.state, 'ready')
        # el recurso se CREÓ una sola vez: el reintento reutilizó el existente
        self.assertEqual(counter['creations'], 1)
        self.assertEqual(counter['attempts'], 2)
        # identidad conservada
        self.assertTrue(job.database_name.startswith('chart_env_'))

    def test_05_contract_tasks_job_linked_without_proven_environment(self):
        """Contrato, tareas y job quedan vinculados sin entorno comprobado."""
        manager = self._manager()
        type(self)._seq += 1
        n = type(self)._seq
        partner = self._portal_partner('Cliente Link %d' % n, 'link%d@test.com' % n)
        order = self._make_order(partner, [(self.test_service.product_variant_id, 1)])
        order.action_confirm()
        contract = order.chart_contract_ids
        # tareas del contrato (contrato en 'pending_preparation', sin entorno)
        tasks = contract._ensure_preparation_tasks()
        self.assertTrue(tasks)
        for task in tasks:
            self.assertEqual(task.contract_id, contract)
        # job aprobado y vinculado (requiere pending_activation_approval, NO ready)
        contract.with_context(chart_allow_admin_correction=True).write(
            {'state': 'pending_activation_approval'})
        job = self.env['chart.provisioning.job'].with_user(manager).create(
            {'contract_id': contract.id})
        job.with_user(manager).action_approve()
        self.assertEqual(job.contract_id, contract)
        self.assertEqual(contract.provisioning_job_id, job)
        self.assertEqual(job.state, 'approved')
        self.assertNotEqual(job.state, 'ready')

    def test_06_cancel_and_reset_preserve_identity_and_resources(self):
        """Cancelar o resetear no elimina recursos ni pierde identidad."""
        job, manager, _ = self._approved_job()
        identity = (job.idempotent_key, job.database_name, job.environment_url)
        job.with_user(manager).action_cancel()
        self.assertEqual(job.state, 'cancelled')
        self.assertEqual((job.idempotent_key, job.database_name,
                          job.environment_url), identity)
        job.with_user(manager).action_reset()
        self.assertEqual(job.state, 'draft')
        self.assertEqual((job.idempotent_key, job.database_name,
                          job.environment_url), identity)
        # el borrado de recursos exige confirmación Y autorización específica
        with self.assertRaises(UserError):
            job.with_user(manager).action_delete_environment()
