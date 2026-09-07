# CHART E-COMMERCE IMPLEMENTATION - STATUS REPORT

**Date:** 2026-09-07 20:35 UTC
**Module:** `chart_service_commerce` — **v19.0.2.0.0** (continuación: estabilización, experiencia de cliente y facturación de prueba)
**Estado:** Instalación verificada ✓ · **43 tests en verde ✓** (35 post-install + 8 at-install)
**Git:** 2 feature commits + reescritura y esta continuación sin commitear (ver GIT LOG)

---

## RESUMEN EJECUTIVO

El módulo fue **reescrito por completo** respecto al scaffold inicial (commit `885c795`)
y, en esta continuación, se **estabilizó y amplió** con correcciones de seguridad,
experiencia de cliente y facturación de prueba en staging. Todo verificado en una copia
de la base de staging `chart1_staging` con `--workers=0` y código de salida real.

**Decisiones de diseño corregidas vs. la propuesta anterior:**
- Reutiliza registro, sesión, carrito y `sale.order` **nativos** (`auth_signup` +
  `website_sale`). No hay autenticación paralela ni segundo pedido.
- El contrato se construye desde las **líneas del pedido** (variantes `product.product`,
  cantidades y precios congelados), no desde un Many2many de `product.template`.
- Varios servicios por pedido → preparación/activación **independiente por contrato**.
- Activación = flujo real asistido: contratación → verificación/datos → preparación →
  comprobación de entorno → activación manual. Las 72 h son un objetivo informativo
  (`activation_due_at`, Datetime UTC), **nunca** un disparador automático.
- **Facturación de prueba en staging**: se pueden generar BORRADORES de `account.move`
  sobre períodos persistentes, sin timbrar, enviar ni marcar pagado. Deshabilitado por
  defecto y solo en staging.
- Seguridad: ACL de portal + reglas de propiedad **por titular autorizado** (no
  `child_of` de la empresa); verificación de email con token de un solo uso; sin
  secretos en metadata; datos de prueba solo en `tests/`.

---

## ESTRUCTURA ACTUAL DEL MÓDULO

```
chart_service_commerce/
├── __manifest__.py                 v19.0.2.0.0 · depends: website_sale, portal, auth_signup, sale_management
├── controllers/
│   └── main.py                     Checkout sin pago + portal "Mis soluciones" + verificación de email
├── models/
│   ├── product_template.py         Marca de servicio contratable (is_chart_service)
│   ├── sale_order.py               Términos aceptados + derivación de contratos
│   ├── service_contract.py         Modelo central (estados, activación, verificación, componentes)
│   ├── service_billing_period.py   Períodos facturables persistentes y únicos + borradores
│   ├── economic_component.py       Componentes económicos estructurados por línea
│   ├── provisioning_task.py        Tareas de preparación con cierre manual
│   └── res_partner.py              Invalida verificación de email al cambiar el correo
├── security/
│   ├── chart_service_security.xml  Grupos + reglas de propiedad (portal, titular autorizado)
│   └── ir.model.access.csv         ACLs
├── data/
│   └── ir_sequence_data.xml        Secuencia CS-2026-XXXX
├── views/
│   ├── product_template_views.xml  Campo servicio en ficha de producto
│   ├── service_contract_views.xml  Lista/form/búsqueda + menús backend
│   ├── portal_templates.xml        Portal "Mis soluciones" + verificación de email (QWeb)
│   └── website_sale_templates.xml  Bloque de contratación en carrito + checkout
├── static/src/scss/checkout.scss   Estilos del checkout
└── tests/
    ├── common.py                   Fixture compartido (productos, partners, pedidos)
    ├── test_contract_flow.py       9 tests de flujo de contratación
    ├── test_billing_periods.py     12 tests de períodos facturables + borradores
    ├── test_portal_security.py     6 tests de seguridad de portal
    └── test_email_verification.py  8 tests de verificación de email
```

**Archivos eliminados en la reescritura** (ya no existen): `controllers/checkout.py`,
`controllers/payment.py`, `controllers/portal.py`, `models/service_activation.py`,
`models/account_move_ext.py`, `models/account_move_line_ext.py`,
`models/res_partner_ext.py`, `models/sale_order_ext.py`, `data/email_templates_data.xml`,
`data/ir_cron_data.xml`, `security/record_rules.xml`, y las vistas/templates placeholder
(`checkout_views.xml`, `portal_views.xml`, `provisioning_task_views.xml`,
`account_move_inherit.xml`, `templates/email_verification.xml`,
`templates/activation_confirmed.xml`).

---

## MODELOS

### `chart.service.contract` (models/service_contract.py, ~500 LOC)
Modelo central de contratación de servicios.

- **Nombre** auto-generado por secuencia `CS-2026-XXXX`.
- **Máquina de estados:** `draft → pending_preparation → in_preparation →
  pending_activation_approval → active`, más `canceled`. Ningún cron cambia a `active`.
- **Origen:** se deriva de las líneas de un `sale.order` confirmado
  (`_create_from_order`), agrupando por producto de servicio. Deduplicación por
  `dedup_key` (unique) → doble clic no duplica.
- **Campos clave:** `partner_id`, `product_id`, `periodicity` (none/monthly/quarterly/
  annual), `service_type` (one_time/subscription), `sale_order_id`, `order_line_ids`,
  `amount_recurring/one_time/total`, `currency_id`, `company_id`.
- **Condiciones económicas congeladas:** `economic_terms_snapshot` (texto con pedido,
  servicio, líneas, total y moneda al momento de contratar).
- **Términos aceptados:** `terms_accepted`, `terms_version`, `terms_accepted_datetime`
  (persistidos desde el pedido).
- **Verificación de datos:** `data_complete`/`data_missing` (nombre, email, teléfono,
  ciudad, CP, RFC, email verificado) y `email_verified`.
- **Verificación de email (token de un solo uso):** `email_verification_token`,
  `email_verification_token_expiry`, `email_verification_sent_at`,
  `email_verification_send_count`, `email_verified_recipient`. Métodos
  `action_send_verification_email` (reenvío limitado con enfriamiento),
  `action_verify_email` (valida token, consume, registra destinatario; NO activa nada)
  y `_invalidate_email_verification` (se invalida al cambiar el correo del partner).
- **Preparación:** `task_ids` (One2many a `chart.provisioning.task`), `task_total`,
  `task_done`, `environment_checked`, `environment_checked_date/by`,
  `environment_url` (validada), `environment_note`.
- **Activación:** `activation_date`, `billing_anchor_date`, `activated_by`,
  `activation_due_at` (objetivo informativo de 72 h, Datetime UTC),
  `ready_at` (lista para activar), `state_label` (etiqueta orientada al cliente).
- **Componentes económicos estructurados:** `economic_component_ids` (One2many a
  `chart.service.economic.component`), cada línea clasificada como cargo único o
  recurrente con variante, cantidad, precio, moneda e impuestos congelados.
- **Acciones:** `action_confirm_contract`, `action_start_preparation`,
  `action_mark_environment_checked` (exige URL de entorno validada),
  `action_activate` (manual, exige preparación completa + entorno comprobado + URL),
  `action_cancel`, `action_reset_draft` (bloqueado si activo), `write` (bloquea cambios
  ordinarios del ancla una vez activo).

### `chart.service.billing.period` (models/service_billing_period.py)
Períodos facturables **persistentes y únicos**.

- Calculados por **meses calendario** desde `billing_anchor_date` (no sumando 30 días,
  no recalculando con la fecha del cron).
- **Cada frontera se calcula DIRECTAMENTE desde el ancla** (`B(k) = anchor +
  relativedelta(months=k*meses)`). No se encadenan fechas: encadenar desplaza el ancla
  (31 ene → 28 feb → 28 mar). Política: período k = `[B(k-1), B(k))`, `date_end = B(k)-1`.
- Unicidad en BD: `unique(contract_id, index)` y `unique(contract_id, date_start)`.
- Generación **idempotente** (`generate_periods`): un cron puede correr varias veces
  sin duplicar. Períodos contiguos (sin huecos ni solapes).
- **Borradores de factura (staging):** `action_generate_draft_invoice` crea un
  `account.move` en `draft` para el período (sin timbrar, enviar ni marcar pagado).
  Solo disponible si la config `chart_service_commerce.enable_draft_invoicing=True`
  (por defecto deshabilitado; se habilita solo en staging). `move_id` guarda el borrador,
  `draft_invoice_generated` lo indica, `invoiced` permanece `False`.

### `chart.service.economic.component` (models/economic_component.py)
Componentes económicos **estructurados** por línea de pedido (motor de cobro futuro).

- Cada línea se clasifica por separado como cargo **único** o **recurrente** según la
  periodicidad de su producto. Una solución con implementación + mensualidad genera dos
  componentes; no se clasifica todo el contrato como único o recurrente.
- Congela variante, cantidad, unidad, precio unitario, descuento, subtotal, total,
  moneda e impuestos desde la línea del pedido (`related` a `sale.order.line`).
- Guarda versión y fecha de aceptación de condiciones.

### `chart.provisioning.task` (models/provisioning_task.py)
Tareas de preparación de un contrato.

- **Ninguna tarea se auto-completa ni simula trabajo** (no hay handlers automáticos,
  no se guardan claves en metadata).
- Pasa a `done` **solo** cuando una persona autorizada la verifica y la marca
  manualmente, dejando evidencia (fecha + responsable + nota de cierre obligatoria).
- `code` clasifica el paso (verificación de datos, accesos, importación, usuario,
  onboarding, otra); no ejecuta nada por sí mismo.
- Estados: `pending → in_progress → done/blocked/skipped`. Constraint: `done` exige
  `done_date`, `done_by_id` y `completion_note`.

### `product.template` (models/product_template.py)
Añade `is_chart_service` (marca de servicio contratable sin pago) y
`chart_service_periodicity`.

### `sale.order` / `sale.order.line` (models/sale_order.py)
- `sale.order`: `chart_terms_accepted/version/datetime`, `chart_contract_ids`,
  `chart_contract_count`, `chart_has_services`, `chart_only_services`,
  `chart_service_line_ids`. Métodos `chart_accept_terms()` y `action_confirm()`
  (sobreescrito: confirma nativo y deriva contratos de forma transaccional).
- `sale.order.line`: `chart_contract_id` (campo inverso del One2many del contrato).

---

## CONTROLLERS (controllers/main.py)

### `WebsiteSaleChart` — Checkout de servicios sin pago
Ruta `/shop/chart/confirm` (POST, auth public, website):
- **La confirmación comercial exige identidad autenticada.** Un visitante anónimo
  (usuario público) NO puede confirmar ni asignar contratos: la ruta conserva el
  carrito en la sesión y redirige a `/web/login?redirect=/shop/cart`. Tras autenticarse,
  el carrito se reasigna al partner y el cliente confirma desde el carrito.
- Valida en servidor: carrito con líneas, términos aceptados, presencia de servicios.
- **Idempotente ante doble clic y concurrencia:** si el pedido ya está confirmado
  (`state != 'draft'`) redirige sin volver a confirmar. Solo se absorbe el conflicto
  esperado de doble confirmación (si el pedido NO quedó confirmado, el error se propaga).
- Llama `chart_accept_terms()` + `action_confirm()` y redirige a `/my/soluciones`.

### `CustomerPortalChartSolutions` — Portal "Mis soluciones"
- `/my/soluciones` y `/my/soluciones/page/<int:page>`: listado de contratos del
  partner autenticado (con paginación y filtro por estado).
- `/my/soluciones/<int:contract_id>`: detalle. Búsqueda **sin sudo**: la ACL de portal
  + la regla de propiedad deciden; un contrato ajeno → `MissingError` → redirect.
- `/my/soluciones/<int:contract_id>/verify`: verificación de email desde el enlace del
  correo (auth public, token de un solo uso). NO activa ningún servicio.
- Contador `chart_contract_count` en el home del portal.

---

## SEGURIDAD

- **Grupos** (`chart_service_security.xml`): `group_chart_service_user` (Preparador,
  cierra tareas con evidencia, NO activa) y `group_chart_service_manager`
  (Responsable, único que aprueba la activación real). Usan el patrón de Odoo 19
  (`res.groups.privilege` + `privilege_id`).
- **Regla de propiedad por titular autorizado:** el portal solo ve contratos cuyo
  `partner_id == user.partner_id` (el titular) o que estén en `authorized_partner_ids`
  (autorización EXPLÍCITA). **NO** se usa `child_of` del `commercial_partner_id`, que
  daba acceso a todos los contactos de la empresa sin autorización. Sin permisos de
  escritura/creación. Aplica a contratos, tareas, períodos y componentes.
- **Verificación de email:** token aleatorio de un solo uso con expiración, reenvío
  limitado, invalidación al cambiar el correo. El token nunca se expone en el portal ni
  en logs. Verificar NO activa ningún servicio.
- **URL de entorno validada:** `_validate_environment_url` solo admite http(s) en
  dominios permitidos (no localhost/IPs internas). El botón "Entrar a mi Odoo" solo
  aparece cuando hay un acceso real validado; nunca se muestran credenciales.
- **ACLs** (`ir.model.access.csv`): permisos base por grupo.
- **Sin secretos** en metadata ni campos de texto libre.

---

## VISTAS Y TEMPLATES

- **Backend** (`service_contract_views.xml`): lista, formulario (con pestañas de
  tareas, períodos y snapshot), búsqueda, acciones y menús "Servicios Chart" →
  Contratos / Preparación.
- **Producto** (`product_template_views.xml`): campo "servicio contratable" en la
  ficha de producto (página `general_information`).
- **Website** (`website_sale_templates.xml`): bloque "Contratar servicio sin pago" en
  el carrito (`website_sale.cart_lines`) + template de resumen de contratación
  (`chart_checkout_template`).
- **Portal** (`portal_templates.xml`): enlace "Mis soluciones" en el home del portal,
  listado `portal_my_solutions` y detalle `portal_my_solution_detail`.
- **Estilos** (`static/src/scss/checkout.scss`).

---

## TESTS — 43 EN VERDE ✓

Suite ejecutada en una **copia** de `chart1_staging` (para no interferir con el servidor
de producción que sirve esa base) con `--workers=0` y código de salida real.
Resultado: **43 tests (35 post-install + 8 at-install), 0 errores, 0 fallos, exit 0.**

| Archivo | Tests | Cobertura |
|---------|-------|-----------|
| `test_contract_flow.py` | 9 | Confirmación crea contrato; doble clic no duplica; varios servicios → contratos independientes; variante preservada; montos validados en servidor; activación exige preparación completa + URL de entorno; términos persistidos; contrato reaparece tras relogin; componentes económicos estructurados |
| `test_billing_periods.py` | 12 | Períodos mensuales contiguos desde el ancla; regeneración idempotente; sin ancla no genera; cargo único sin períodos; no emite factura; **ancla 31 ene sin deriva**; días 28/29/30/31; año bisiesto; 12 ciclos sin deriva; regeneración parcial conserva ancla; borrador deshabilitado por defecto; generación de borradores |
| `test_portal_security.py` | 6 | Cliente A no lee contrato de B; A lee su propio contrato; portal no puede escribir; **contactos de la misma empresa aislados sin autorización**; miembro autorizado explícitamente sí lee; tareas/períodos aislados |
| `test_email_verification.py` | 8 | Envío genera token con expiración; verificación con token correcto; token incorrecto falla; token de un solo uso; token caducado falla; límite de reenvío; cambio de email invalida; verificar no activa |

---

## VERIFICACIÓN Y CORRECCIONES REALIZADAS (2026-09-07)

Durante la verificación de instalación en `chart1_staging` se detectaron y corrigieron
los siguientes defectos que impedían instalar el módulo o pasar los tests:

**Bugs de instalación (críticos):**
1. `service_contract.py`: `user_id` era `related='partner_id.user_ids'` (One2many) en
   un campo Many2one → convertido a campo computado que toma el primer usuario del
   partner.
2. `sale_order.py`: faltaba el modelo `sale.order.line` con el campo inverso
   `chart_contract_id` requerido por el One2many `order_line_ids` del contrato.
3. `chart_service_security.xml`: usaba el patrón antiguo (`ir.module.category` +
   `category_id` + `users`) → migrado al patrón de Odoo 19 (`res.groups.privilege` +
   `privilege_id` + `user_ids`).
4. `product_template_views.xml`: XPath apuntaba a `//page[@name='general_info']` →
   corregido a `general_information`.

**Bugs de tests:**
5. `common.py`: campos inexistentes `available_in_pos` y `public_cats_ids` →
   eliminado el primero, corregido a `public_categ_ids`; generación de variantes con
   `_create_variant_ids()` y búsqueda correcta por
   `product_template_attribute_value_ids.product_attribute_value_id`.
6. `common.py`: partners de prueba sin datos completos (teléfono, ciudad, CP, RFC) →
   añadidos al fixture.
7. `test_billing_periods.py` / `test_contract_flow.py`: `action_activate` exige
   `email_verified` → marcado en los helpers de test.
8. `test_contract_flow.py`: `test_02` llamaba `action_confirm` dos veces (Odoo nativo
   lo rechaza) → verifica que el segundo intento lanza `UserError` sin duplicar;
   `test_07` buscaba `'1500'` en un snapshot con formato de moneda → aserción robusta;
   `test_08` usaba `self.env.cr.rollback()` (prohibido en tests) → re-lectura del
   registro.
9. `service_billing_period.py`: `generate_periods` producía huecos con ancla en día 31
   → ahora encadena períodos contiguos (`start_{i+1} = end_i + 1 día`); test ajustado
   a la semántica contigua.

**Comando de verificación:**
```bash
odoo -c /opt/odoo/staging/odoo-staging.conf -d chart1_staging \
     -u chart_service_commerce --test-enable --stop-after-init
# Resultado: chart_service_commerce: 22 tests, 0 errores, 0 fallos
```

---

## ESTADO POR ETAPAS (PLAN ORIGINAL)

| Etapa | Estado |
|-------|--------|
| Stage 1 — Homepage (chart_websales) | ✓ Completada |
| Stage 2 — Scaffold & modelos | ✓ Completada (reescrita a v19.0.2.0.0) |
| Stage 3 — Validación de modelos | ✓ Completada (43 tests en verde) |
| Stage 4 — Checkout sin pago | ✓ Implementado (controller + templates + guard de identidad) |
| Stage 5 — Preparación & tareas | ✓ Implementado (backend + flujo manual + URL de entorno) |
| Stage 6 — Portal & períodos | ✓ Implementado (portal + períodos sin deriva + borradores en staging) |
| Stage 7 — Seguridad, tests, docs | ✓ Seguridad (aislamiento portal, verificación email), tests y docs |

**Pendiente / fuera de alcance de esta entrega:**
- Timbrado/envío real de facturas (los borradores están listos en staging; el timbrado
  y envío requieren configuración fiscal y autorización).
- Pruebas de humo en la base de producción `chart1` (solo se verificó en staging).
- Separación de infraestructura staging/producción (cambios preparados, NO aplicados).

---

## CAMBIOS DE ESTA CONTINUACIÓN (2026-09-07, v19.0.2.0.0)

Correcciones y ampliaciones realizadas sobre la reescritura, verificadas con la suite
completa en verde:

1. **Períodos sin deriva del ancla** (`service_billing_period.py`): cada frontera se
   calcula directamente desde el ancla (`B(k) = anchor + relativedelta(months=k*meses)`),
   no encadenando fechas. Corrige el desplazamiento 31 ene → 28 feb → 28 mar.
2. **Checkout exige identidad** (`controllers/main.py`): un usuario público NO confirma
   ni asigna contratos; la ruta conserva el carrito y redirige a registro/login. Manejo
   de concurrencia (solo absorbe el conflicto esperado de doble confirmación).
3. **Aislamiento del portal** (`security/chart_service_security.xml`): regla de propiedad
   por titular autorizado (`partner_id == user.partner_id` o `authorized_partner_ids`),
   en lugar de `child_of` del `commercial_partner_id`. Aplica a contratos, tareas,
   períodos y componentes. Nuevo campo `authorized_partner_ids`.
4. **Activación y portal** (`service_contract.py`, vistas): `activation_due_at` y
   `ready_at` como Datetime (UTC), presentados en `America/Mexico_City`; `state_label`
   orientado al cliente; `environment_url` validada; bloqueo de cambios ordinarios del
   ancla y de retorno a borrador una vez activo; botón "Entrar a mi Odoo" solo con acceso
   real validado.
5. **Verificación de email real** (`service_contract.py`, `res_partner.py`, controller,
   template): token aleatorio de un solo uso con expiración, reenvío limitado,
   invalidación al cambiar el correo, destinatario verificado como evidencia. Verificar
   NO activa nada.
6. **Componentes económicos estructurados** (`economic_component.py`): cada línea de
   pedido se clasifica como cargo único o recurrente con variante, cantidad, precio,
   moneda e impuestos congelados.
7. **Borradores de factura en staging** (`service_billing_period.py`): genera
   `account.move` en `draft` sobre períodos persistentes, sin timbrar/enviar/pagar.
   Deshabilitado por defecto (config `enable_draft_invoicing`), solo en staging.
8. **Separación staging/producción** (auditoría): producción sirve chart1 Y
   chart1_staging (sin `dbfilter`); staging y producción comparten addons_path. Cambios
   preparados en `/opt/odoo/staging/prepared_prod_changes/` (NO aplicados).

---

## GIT LOG

```
6e70d52 docs: add comprehensive implementation status report
885c795 feat(chart_service_commerce): initialize module structure, models, security, and data
a6793b5 chore(chart_websales): hide category menu from shop page
767766c fix(chart_websales): repair shop layout and add product imagery
5d8cbbe chore: gitignore __pycache__
```

> Nota: la reescritura a v19.0.2.0.0 y las correcciones de esta continuación están
> **sin commitear** (working tree). Ver `git status` para el detalle de archivos
> modificados/eliminados/añadidos.

---

## VERIFICACIÓN DE ESTA CONTINUACIÓN (2026-09-07)

**Comando de verificación** (sobre una copia temporal de `chart1_staging`, para no
interferir con el servidor de producción que sirve esa base):

```bash
createdb -O odoo chart1_test_tmp
pg_dump chart1_staging | psql -q chart1_test_tmp
odoo -c /opt/odoo/staging/odoo-staging.conf -d chart1_test_tmp \
     -u chart_service_commerce --test-enable --stop-after-init --workers=0
# Resultado: chart_service_commerce: 43 tests, 0 errores, 0 fallos, exit 0
dropdb chart1_test_tmp
```

**Defectos detectados y corregidos durante la verificación:**
1. `economic_component.py`: campos `related` con tipo inconsistente (name Char→Text,
   price_unit Monetary→Float) y nombres de campo inexistentes en Odoo 19
   (`product_uom`→`product_uom_id`, `tax_id`→`tax_ids`).
2. Reglas de portal: `('authorized_partner_ids', 'in', [user.partner_id])` no se adapta
   en SQL → `[user.partner_id.id]`.
3. `service_billing_period.py`: `account.account` no tiene `company_id` en Odoo 19 →
   búsqueda de cuenta de ingresos sin filtro de compañía.
4. Tests: helpers ajustados a los nuevos requisitos (URL de entorno para activar,
   corrección explícita del ancla, emails únicos por test).
