# RUNBOOK — VERIFICACIÓN VISUAL DEL LOGIN (CORREGIDO — 2026-09-08)

**Contexto:** intervención de recuperación de acceso y consolidación de CHART (2026-09-08).

**Causa raíz confirmada (con evidencia de servidor, Nº de ref.: RESOLUCION_LOGIN_TESTS_HTTP):**
el login de sesión limpia es funcional; el centro vacío del propietario se debe a que su
**sesión quedó persistida en modo `debug=tests`** (parámetro `?debug=tests`) y, al servirse la
página por **HTTP (contexto no seguro)**, el bundle de pruebas `web.__assets_tests_call__.js`
carga el tour `snippet_editor_panel_options.js` que en su línea 30 evalúa
`browser.navigator.clipboard.writeText` sobre `navigator.clipboard === undefined`. El `TypeError`
aborta el arranque de módulos JS y `web.user_switch` nunca quita `d-none` del formulario.

> ⚠️ **No es caché, ni una extensión, ni "equipo del cliente".** La cadena está documentada en
> `RESOLUCION_INCIDENTE_LOGIN_CHART.md`. El servidor **no** corre en modo tests
> (`test_enable` ausente) y sirve el bundle min correcto (`web.assets_frontend_lazy.min.js`,
> checksum `28aef5f`) que **no** incluye el tour y sí incluye `web.user_switch`.

## 1. Criterios de aceptación (sesión limpiA / incógnito, sin `?debug`)

- [ ] `http://chart.lat:8069/web/login` abre **directamente** el formulario de login, sin selector de base.
- [ ] Se ven los campos **Correo electrónico**, **Contraseña** y el botón **Iniciar sesión**.
- [ ] Logo y colores del sitio (azul navy, dorado) intactos; cabecera y pie presentes.
- [ ] Una contraseña incorrecta muestra "Nombre de usuario o contraseña incorrectos".
- [ ] El propietario entra con sus credenciales reales **sin compartirlas**.
- [ ] El usuario interno entra al backend; el cliente portal entra a su cuenta.
- [ ] `/shop` conserva filtros y productos; las imágenes de producto cargan.
- [ ] `?db=chart1` no permite acceder a `chart1` (sirve siempre `chart1_staging`).

## 2. Pasos en Chrome (ventana de incógnito, F12 abierto)

1. Abrir **ventana de incógnito** (Ctrl+Shift+N) → sesión limpia, sin `debug` persistido.
2. Abrir DevTools (F12) → Consola y Red.
3. Navegar a `http://chart.lat:8069/web/login`.
4. Verificar:
   - El documento `/web/login` responde **200** (no 303/selector).
   - **No** se carga ningún bundle `debug/*` ni `web.__assets_tests_call__.js`. Los assets deben ser
     `web.assets_frontend.min.css`, `web.assets_frontend.min.js`, `web.assets_frontend_lazy.min.js`,
     `web.assets_frontend_minimal.min.js` (versiones `.min`, no `debug`).
   - Consola **sin errores de JavaScript** (nada de `TypeError ... writeText`).
5. Confirmar que el formulario es **visible** (el `d-none` es retirado por `web.user_switch`).
6. Probar login con contraseña incorrecta → `alert-danger`.
7. Probar login correcto del propietario y del cliente portal.
8. Abrir `/shop` y comprobar imágenes/filtros.

## 3. Si el formulario NO se muestra (sesión con tests / HTTP) — resolución ya identificada

La causa **no** es caché, extensión ni equipo del cliente. La cadena real es:

1. La sesión tiene `debug=tests` persistido (por un acceso previo con `?debug=tests`).
2. `web.frontend_layout` (web/views/webclient_templates.xml, L55) llama a `web.conditional_assets_tests`,
   que inyecta los tours de pruebas **solo si** `'tests' in debug` (L6).
3. El bundle `web.__assets_tests_call__.js` carga `snippet_editor_panel_options.js`; su **L30**
   ejecuta en el arranque `const oldWriteText = browser.navigator.clipboard.writeText;`.
4. Por HTTP (`window.isSecureContext === false`), `navigator.clipboard` es `undefined`
   (`web/static/src/core/browser/browser.js` L45 lo expone directamente) → `TypeError`.
5. El `TypeError` aborta el arranque del módulo → `web.user_switch`
   (`web/static/src/core/user_switch/user_switch.js` L17-18, registro L51) no llega a ejecutarse
   → `form.oe_login_form` conserva `d-none` → centro vacío.

**Acción inmediata del propietario:** cerrar la sesión persistida (incógnito/sin cookies, o quitar
cualquier `?debug=tests`) y acceder por HTTPS cuando esté disponible. No es necesario borrar caché.

## 4. Resultado esperado del servidor (verificado)

- `/web/login` limpio → **200** sin selector (`db_name=chart1_staging`, `dbfilter`, `list_db=False`).
- Bundle min `web.assets_frontend_lazy.min.js` (28aef5f): contiene `web.user_switch` y **no**
  contiene `snippet_editor_panel_options`.
- `?db=chart1` → fuerza `chart1_staging`.
- Login incorrecto → `alert-danger` "Nombre de usuario o contraseña incorrectos".
- Imágenes de producto y logo → **200** con bytes reales (filestore migrado).

> Cambios de infraestructura/código adicionales (HTTPS en proxy real, y endurecimiento para que
> `?debug=tests` no rompa el login público) quedan **pendientes de aprobación** — ver
> `RESOLUCION_INCIDENTE_LOGIN_CHART.md` §"Cambios pendientes de aprobación".

---
*Fin del runbook (corregido).*