# RESOLUCIÓN DE INCIDENTE — LOGIN SIN FORMULARIO (CHART)

**Referencia:** RUNBOOK_VERIFICACION_VISUAL_LOGIN.md (corregido)
**Fecha:** 2026-09-08 · **Base:** chart1_staging · **Servidor:** local `172.31.16.14` (Odoo 19)
**Método:** lecturas de código core/custom, configuración systemd, plantillas de assets,
reproducción HTTP real contra el puerto 8069 y correlación con el log de Odoo.

> Clasificación de evidencia: **OBSERVADO** = verificado directamente aquí;
> **REPORTADO** = del propietario/consola, sin verificación visual posible (no hay navegador en el host).

---

## 1. RESUMEN EJECUTIVO

El formulario de login **sí está en el HTML** (`form.oe_login_form` presente en `/web/login`),
nace con la clase nativa `d-none` y la muestra `web.user_switch`. En **sesión limpia el login
funciona**: el bundle min (`web.assets_frontend_lazy.min.js`, checksum `28aef5f`) contiene
`web.user_switch` y **no** contiene el tour de pruebas frágil.

El centro vacío que sigue reportando el propietario (REPORTADO, coincidente con el log) se debe a
que **su sesión quedó persistida en modo `debug=tests`** y, al servirse por **HTTP**, el bundle de
pruebas `web.__assets_tests_call__.js` carga `snippet_editor_panel_options.js`, que en su L30
evalúa `browser.navigator.clipboard.writeText`. En contexto no seguro
(`window.isSecureContext === false`) `navigator.clipboard === undefined`, se lanza un `TypeError`
que **aborta el arranque del módulo** antes de que `web.user_switch` retire `d-none`.

La infraestructura crítica ya está aplicada y correcta: `db_name=chart1_staging`,
`dbfilter=^chart1_staging$`, `list_db=False`, y el proceso **no** corre con `--test-enable`
(no inyecta tours a sesiones limpias).

---

## 2. CADENA REAL (archivo y línea, con evidencia)

| Paso | Archivo:Línea | Hecho verificado |
|---|---|---|
| 1 | `web/views/webclient_templates.xml` L55 (`web.frontend_layout`) | Incluye `web.conditional_assets_tests` (frontend público, incluido el login). |
| 2 | `web/views/webclient_templates.xml` L5-15 | `conditional_assets_tests` inyecta `web.__assets_tests_call__` **solo si** `'tests' in debug or test_mode_enabled`. |
| 3 | `web/views/webclient_templates.xml` L306 | El mismo condicional se usa en el webclient backend (contexto autenticado). |
| 4 | `web/__manifest__.py` L432-438 | `web.__assets_tests_call__` incluye `web.assets_tests` (en L425-431, `web/static/tests/tours/**`). |
| 5 | `web/static/tests/tours/snippet_editor_panel_options.js` **L30** | `const oldWriteText = browser.navigator.clipboard.writeText;` — se ejecuta en el arranque del módulo. |
| 6 | `web/static/src/core/browser/browser.js` L45 | `browser.navigator` ES el `navigator` global. Bajo HTTP, `navigator.clipboard === undefined`. |
| 7 | — (reproducción HTTP) | `http://chart.lat:8069` → contexto NO seguro → `navigator.clipboard` `undefined` → `TypeError: Cannot read properties of undefined (reading 'writeText')`. |
| 8 | Consecuencia del boot | El `TypeError` del tour aborta el arranque de la cadena de módulos en la sesión `debug/tests`. |
| 9 | `web/static/src/core/user_switch/user_switch.js` L17-18, L51 | `web.user_switch` (L51) haría `form.oe_login_form.classList.toggle("d-none", ...)`; **no llega a ejecutarse** → el formulario conserva `d-none`. |

**Resultado:** centro de login aparentemente vacío con cabecera y pie.

---

## 3. POR QUÉ SE CARGA CÓDIGO DE TESTS EN EL LOGIN

- **No** es un registro `ir.asset` propio: no hay ninguna cadena de `snippet_editor_panel_options`,
  `highlight_utils`, `website_root`, `text_highlights`, `tests/tours` ni `writeText` en
  `chart_websales` ni en `chart_service_commerce` (grep en `/opt/odoo/custom_addons` dio 0 resultados).
- **No** es el tema: `theme_cobalt` es core y no referencia esos tours.
- Es la **plantilla core** `web.frontend_layout` → `web.conditional_assets_tests`, disparada
  únicamente por **`'tests' in debug`** (sesión) o por **`test_mode_enabled`** (servidor `--test-enable`).
- El proceso **no** corre con `--test-enable` (OBSERVADO en `/proc/591/cmdline` y en
  `/etc/odoo/odoo.conf`: sin `test_enable`). Por tanto, la única vía es la **sesión** del navegador
  con `debug=tests`.
- Log OBSERVADO: la IP del propietario (`189.228.155.176`) pide repetidamente
  `GET /web/assets/1/debug/web.assets_frontend_lazy.js`,
  `GET /web/assets/1/debug/web.__assets_tests_call__.js`, etc. → sesión en modo tests/debug.

## 4. DEPENDENCIAS "FALTANTES" — NO SON ARCHIVOS AUSENTES

Los módulos que la consola marca como "no cargan" **existen** en
`/usr/lib/python3/dist-packages/odoo/addons` (la ruta real de `addons_path`):
- `website/static/src/js/content/website_root.js` (6460 B) — existe.
- `website/static/src/js/highlight_utils.js` (28346 B) — existe.
- `website/static/src/js/content/website_root_instance.js` (alias **`root.widget`**, 519 B) — existe.
- `website/static/src/interactions/text_highlights.js` y `.preview` — existen e importan
  `@website/js/highlight_utils` (verificado en su cabecera).

**No se crean módulos vacíos ni se copia JS de otra versión.** Son síntoma del boot abortado por
el `TypeError` del tour, no archivos faltantes.

> ⚠️ Existe una **segunda copia** de addons en `/usr/lib/python3/dist-packages/addons` (sin
> `odoo/`) **vacía de `static`**. Esa ruta **NO está** en `addons_path` (solo
> `/usr/lib/python3/dist-packages/odoo/addons` y `/opt/odoo/custom_addons`), por lo que **no
> afecta** al servidor. Es resto del paquete apt; no debe usarse ni borrarse por este incidente.

## 5. HTTP Y HTTPS

- Verificar en el navegador del propietario:
  - `window.isSecureContext` → `false` en `http://chart.lat:8069`.
  - `typeof navigator.clipboard` → `undefined`.
- El puerto 8069 sirve **HTTP plano**. No recomendar `https://chart.lat:8069` como solución; el
  HTTPS debe salir de un **proxy reverso real** (Apache/nginx) delante del 8069, con certificado
  válido. En este host **no hay** Apache/nginx (OBSERVADO), por lo que es un **cambio de
  infraestructura pendiente de aprobación** (ver §6). HTTPS **no sustituye** la corrección del
  arranque ni elimina los tours; solo evitaría que `navigator.clipboard` sea `undefined`.

## 6. CAMBIOS PENDIENTES DE APROBACIÓN (no aplicados en esta intervención)

1. **HTTPS en proxy real (público)** para hacer `window.isSecureContext === true`, con
   certificado válido y **sin** afectar otros servicios; `chart.lat` debe apuntar a la capa pública.
2. **Endurecimiento con foco (módulo propio, no core):** override de `web.conditional_assets_tests`
   para que, en **producción** (`/etc/odoo/odoo.conf` sin `test_enable`), no se inyecte la bundle
   de pruebas en el **frontend público** si una sesión vuelve a quedar en `debug=tests`. Así el
   login no puede romperse por un `?debug=tests` residual.
   > Requiere decisión: si al equipo le interesa mantener `?debug=tests` públicos, se descarta.
   > No se elimina ningún tour; solo se controla su inyección.
3. **Diagnóstico visual** (no automatizable aquí): el propietario corre los pasos del runbook
   corregido en **incógnito** (sesión limpia), que ya funcionan.

## 7. RESTRICCIONES RESPETADAS

- No se quita `d-none` globalmente. · No se añade CSS para forzar el formulario.
- No se modifica el core ni `theme_cobalt`. · No se añade autenticación alternativa.
- No se cambian colores/tipografías/layout. · No se resetean contraseñas.
- No se borraron `ir.attachment`, filestore, imágenes ni documentos.
- No se fusionaron bases ni se desarrollaron nuevas funciones.
- No se crearon módulos vacíos ni se copió JS de otra versión de Odoo.

## 8. VERIFICACIÓN Y LÍMITES

- **Sí disponible aquí:** HTML login limpio (200), bundle min `web.assets_frontend_lazy.min.js`
  (200, contiene `web.user_switch`, sin el tour), config del servidor y log de la sesión tests.
- **NO disponible aquí:** no hay navegador (chromium/firefox/playwright) en el servidor, por lo
  que **no se declara recuperación visual**: se entrega el root-cause, el runbook corregido y la
  comprobación breve para el propietario (incógnito, sesión limpia). La recuperación completa se
  cierra cuando el propietario confirme el formulario visible y el carrito conservado tras el login.

---
*Fin de la resolución. Runbook corregido en paralelo.*
