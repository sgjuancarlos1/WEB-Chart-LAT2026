# RUNBOOK — VERIFICACIÓN VISUAL DEL LOGIN (PENDIENTE EN CHROME)

**Contexto:** intervención de recuperación de acceso y consolidación de CHART (2026-09-08).
El servidor no dispone de navegador (ni chromium/firefox/playwright), por lo que la
comprobación **visual** del formulario de login queda PENDIENTE y debe hacerse en el
navegador del propietario. Este documento es el procedimiento concreto.

## 1. Qué se pide demostrar (criterios de aceptación)

En una **sesión limpia** (ventana privada / incógnito, sin cookies), el propietario debe comprobar:

- [ ] `https://<host>:8069/web/login` abre **directamente el formulario**, sin selector de base de datos.
- [ ] Se ven los campos **Correo electrónico**, **Contraseña** y el botón **Iniciar sesión**.
- [ ] Logo y colores del sitio (azul navy, dorado) intactos; cabecera y pie presentes.
- [ ] Una contraseña incorrecta muestra el mensaje **"Nombre de usuario o contraseña incorrectos"** (ya comprobado por servidor).
- [ ] El propietario entra con sus credenciales reales SIN compartirlas ni registrarlas en logs.
- [ ] El usuario interno entra al backend; el cliente portal entra a su cuenta.
- [ ] `/shop` conserva filtros y productos; las imágenes de producto cargan.
- [ ] Cambiar el parámetro `?db=chart1` **no** permite acceder a `chart1` (sirve siempre `chart1_staging`).

## 2. Pasos en Chrome (ventana de incógnito, F12 abierto)

1. Abrir una **ventana de incógnito** (Ctrl+Shift+N) para garantizar sesión limpia.
2. Abrir **DevTools** (F12) → pestaña **Consola** y pestaña **Red**.
3. Navegar a `http://<host>:8069/web/login`.
4. Observar:
   - En **Red**: el documento `/web/login` debe responder **200** (no 303/selector).
   - En la **Consola**: **sin errores de JavaScript** (en particular ningún `TypeError` previo a `user_switch`).
5. Verificar que el formulario esté **visible** (la clase `d-none` debe ser retirada por `web.user_switch`).
6. Probar un login con contraseña incorrecta y confirmar el mensaje de error en el `alert-danger`.
7. Probar login correcto del propietario y del cliente portal.
8. Abrir `/shop` y comprobar imágenes/filtros.

## 3. Si el formulario NO se muestra (fallo JS) — diagnóstico a posteriori

El bundle del login (`web.assets_frontend_lazy.min.js`) fue verificado por servidor:
- Responde **200** con `application/javascript` (2.5 MB).
- Contiene `web.user_switch`, `public_components` y `getLastConnectedUsers`.
- **Es JavaScript sintácticamente válido** (parseado con `node --check`) en ambas bases.
- No hay JS propio en `chart_websales` ni `chart_service_commerce` (solo SCSS), por lo que
  **no hay un error de asset propio** capaz de romper los `public.interactions`.

Si aún así el formulario no aparece en pantalla, la causa sería **cliente/específica del
entorno** (extensión, caché, o interrupción antes de montar `user_switch`). En ese caso:
- **No** quitar `d-none` globalmente ni desactivar `web.user_switch` a ciegas.
- **No** crear autenticación alternativa.
- Registrar el error exacto de consola como evidencia antes de proponer un parche en módulo propio.

## 4. Resultado esperado del servidor (ya comprobado)

- `/web/login` limpio → **200** sin selector.
- `?db=chart1` → fuerza **chart1_staging** (chart1 fuera de servicio web vía `dbfilter`).
- Login incorrecto → `alert-danger` "Nombre de usuario o contraseña incorrectos".
- Imágenes de producto y logo → **200** con bytes reales (filestore migrado).

---
*Fin del runbook.*