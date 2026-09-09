# -*- coding: utf-8 -*-
"""Ejecutor de inicialización del entorno cliente (se ejecuta con ``odoo shell``).

Este script corre DENTRO de la base destino recién creada (``chart_env_*``),
con el binario/config/data_dir del EJECUTOR dedicado, y completa la
configuración que ``-i`` no puede hacer por sí sola: empresa del cliente,
cuenta de acceso con su contraseña temporal e invitación firmada con caducidad.

Contrato con ``chart.provisioning.job`` (``models/provisioning_job.py``):
- Entrada: variable de entorno ``CHART_ENV_SETUP_JSON`` (JSON). Los datos del
  cliente nunca se interpolan en un comando ni viajan en argv.
- Salida: una línea ``CHART_ENV_RESULT: {json}`` en stdout con
  ``admin_username``, ``invitation_token`` e ``invitation_expires``.
- Reporte: ``<data_dir del ejecutor>/chart_env_reports/<base>.json``, que
  acredita odoo_initialized, modules_installed, company_configured,
  user_created, filestore_ready, invitation_ok y environment_url. El job no
  deja un entorno ``ready`` sin ese reporte.

Seguridad y recuperación:
- ``odoo shell`` hace ROLLBACK al salir (``odoo/cli/shell.py``): este script
  hace commit explícito SÓLO cuando todo el setup terminó y validó. Un fallo
  deja la base sin cambios parciales y el trabajo en ``failed``; el reintento
  del job reconcilia sin duplicar (el script es idempotente).
- La contraseña temporal viaja únicamente por variable de entorno: nunca se
  imprime, se registra ni se persiste en metadata. El token de invitación sí
  se devuelve al job (canal para que el cliente establezca SU contraseña).
- El reporte se escribe con permisos 0600 dentro de un directorio 0700.
- El script se niega a correr si la base no cumple el patrón aislado
  ``chart_env_`` o si el payload no corresponde a la base en la que corre.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta

from odoo import tools

_DB_ENV_RE = re.compile(r'^chart_env_[a-z0-9]{12}$')
_REPORTS_SUBDIR = 'chart_env_reports'
_INVITATION_HOURS = 72
_MODULES_TO_VERIFY = ('base', 'web', 'mail', 'auth_signup', 'portal',
                      'website', 'website_sale')
_RESULT_MARKER = 'CHART_ENV_RESULT:'
_REPORT_KEYS = ('odoo_initialized', 'modules_installed', 'company_configured',
                'user_created', 'filestore_ready', 'invitation_ok',
                'environment_url')


def _abort(message):
    """Sale con error SIN commit: la base queda sin cambios parciales.

    La salida acotada del subproceso puede quedar en registros del operador;
    por eso los mensajes nunca incluyen secretos ni el payload completo.
    """
    sys.stderr.write('environment_setup: %s\n' % message)
    sys.exit(1)


def _load_payload():
    raw = os.environ.get('CHART_ENV_SETUP_JSON')
    if not raw:
        _abort('falta la variable CHART_ENV_SETUP_JSON con el payload del setup.')
    try:
        payload = json.loads(raw)
    except ValueError:
        _abort('el payload CHART_ENV_SETUP_JSON no es JSON válido.')
    if not isinstance(payload, dict):
        _abort('el payload CHART_ENV_SETUP_JSON no es un objeto JSON.')
    return payload


def _configure_company(env, payload):
    company = env['res.company'].search([], order='id', limit=1)
    if not company:
        _abort('la base destino no tiene compañía principal.')
    company_name = str(payload.get('company_name') or '').strip()[:120]
    company_email = str(payload.get('company_email') or '').strip()[:120]
    if not company_name:
        _abort('el payload no incluye company_name.')
    vals = {'name': company_name}
    if company_email:
        vals['email'] = company_email
    company.write(vals)
    return company, company_name


def _ensure_admin_user(env, payload):
    """Crea o reconcilia la cuenta de acceso del entorno (idempotente)."""
    admin_login = str(payload.get('admin_login') or '').strip()
    admin_email = str(payload.get('admin_email') or '').strip()
    admin_name = str(payload.get('admin_name') or '').strip() or 'Cliente'
    admin_password = payload.get('admin_password') or ''
    if not admin_login:
        _abort('el payload no incluye admin_login.')
    if not admin_password:
        _abort('el payload no incluye admin_password.')

    Users = env['res.users']
    Partners = env['res.partner']
    user = Users.search([('login', '=', admin_login)], limit=1)
    if user:
        # Reintento: misma operación, mismo login. Se actualiza la contraseña
        # temporal (el job la regenera en cada intento y no la conserva).
        vals = {'name': admin_name}
        if admin_password:
            vals['password'] = admin_password
        user.write(vals)
        return user

    partner = Partners
    if admin_email:
        partner = Partners.search([('email', '=ilike', admin_email)], limit=1)
    if partner and partner.user_ids:
        _abort('el correo del cliente ya pertenece a otra cuenta de este '
               'entorno; no se secuestra el partner.')
    if not partner:
        partner = Partners.create({'name': admin_name,
                                   'email': admin_email or False})
    return Users.create({
        'name': admin_name,
        'login': admin_login,
        'email': admin_email or False,
        'password': admin_password,
        'partner_id': partner.id,
        'group_ids': [(6, 0, [env.ref('base.group_user').id])],
    })

def _create_invitation(env, user):
    """Token firmado de invitación con caducidad (auth_signup de Odoo 19)."""
    partner = user.partner_id
    partner.write({'signup_type': 'reset'})
    token = partner.sudo()._generate_signup_token(expiration=_INVITATION_HOURS)
    expires = datetime.utcnow() + timedelta(hours=_INVITATION_HOURS)
    expires_str = expires.strftime(tools.DEFAULT_SERVER_DATETIME_FORMAT)
    return token, expires_str


def _missing_modules(env):
    expected = set(_MODULES_TO_VERIFY)
    modules = env['ir.module.module'].search([('name', 'in', list(expected))])
    installed = {m.name for m in modules if m.state == 'installed'}
    return sorted(expected - installed)


def _write_report(data_dir, db_name, report):
    """Escribe el reporte post-setup (permisos 0700/0600, tras el commit)."""
    if not data_dir:
        _abort('sin data_dir del ejecutor no se puede dejar el reporte.')
    reports_dir = os.path.join(data_dir, _REPORTS_SUBDIR)
    try:
        os.makedirs(reports_dir, mode=0o700, exist_ok=True)
        os.chmod(reports_dir, 0o700)
    except OSError as exc:
        _abort('no se pudo preparar %s: %s' % (_REPORTS_SUBDIR, exc))
    report_path = os.path.join(reports_dir, '%s.json' % db_name)
    try:
        fd = os.open(report_path,
                     os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(report, fh, indent=2, sort_keys=True)
        os.chmod(report_path, 0o600)
    except OSError as exc:
        _abort('no se pudo escribir el reporte de verificación: %s' % exc)


def _emit_result(user, token, expires_str):
    # Única salida de datos; el job la parsea. Nunca incluye la contraseña.
    result = {
        'admin_username': user.login,
        'invitation_token': token,
        'invitation_expires': expires_str,
    }
    print('%s %s' % (_RESULT_MARKER, json.dumps(result)))


def main(env):
    db_name = env.cr.dbname
    if not _DB_ENV_RE.match(db_name):
        _abort('este script solo corre en bases aisladas chart_env_* '
               '(base actual: %s).' % db_name)
    payload = _load_payload()
    # Doble barrera: el payload debe corresponder a la base en la que corre.
    if payload.get('database_name') != db_name:
        _abort('el payload no corresponde a la base destino %s.' % db_name)
    env = env.sudo()

    # 1) Cambios transaccionales: empresa, cuenta de acceso e invitación.
    company, company_name = _configure_company(env, payload)
    user = _ensure_admin_user(env, payload)
    token, expires_str = _create_invitation(env, user)

    # 2) Verificaciones objetivas: nada se da por hecho sin comprobarlo.
    admin_login = str(payload.get('admin_login') or '').strip()
    company_configured = bool(company) and (company.name or '').strip() == company_name
    user_created = bool(user) and user.login == admin_login
    missing_modules = _missing_modules(env)
    env_url = str(payload.get('environment_url') or '').strip()
    data_dir = os.path.expanduser(tools.config.get('data_dir') or '')
    filestore_path = os.path.join(data_dir, 'filestore', db_name) if data_dir else ''
    filestore_ready = bool(filestore_path and os.path.isdir(filestore_path)
                           and os.access(filestore_path, os.W_OK))
    invitation_ok = bool(token and expires_str)

    if not company_configured:
        _abort('la configuración de la empresa no quedó aplicada.')
    if not user_created:
        _abort('la cuenta de acceso no quedó creada.')
    if missing_modules:
        _abort('módulos requeridos no instalados: %s.'
               % ', '.join(missing_modules))
    if not env_url:
        _abort('el payload no incluye environment_url; no hay destino que '
               'acreditar en el reporte.')

    # 3) Punto de no retorno: todo validado => commit explícito (el shell
    #    haría rollback al salir y los cambios se perderían sin este commit).
    env.cr.commit()

    report = {
        'odoo_initialized': True,
        'modules_installed': not missing_modules,
        'company_configured': company_configured,
        'user_created': user_created,
        'filestore_ready': filestore_ready,
        'invitation_ok': invitation_ok,
        'environment_url': env_url,
    }
    if not all(report.get(key) for key in _REPORT_KEYS):
        _abort('el reporte de verificación no quedó completo.')

    # 4) Artefactos POST-commit: reporte (0600) y resultado (sin secretos).
    _write_report(data_dir, db_name, report)
    _emit_result(user, token, expires_str)


if __name__ == '__main__':
    main(env)

