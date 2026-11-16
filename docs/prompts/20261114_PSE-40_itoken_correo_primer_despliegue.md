# Registro de Conversación IA (CHIA) - iToken por correo y opciones del primer despliegue

**Fecha:** 14 de Noviembre, 2026
**Historia:** PSE-40 (despliegue) + mejora del MFA de PSE-4
**Rama:** `feature/PSE-40`
**Herramienta IA:** Claude Code (Claude Opus 5.5)

## Conversación (resumen)
1. El equipo quiere desplegar producción **sin usuarios ni clientes de prueba** e ir cargándolos desde el servidor,
   asignando roles en la consola de Keycloak. Se confirmó que el sistema lee los roles de Keycloak en cada inicio de sesión.
2. Se consultó por qué la contraseña del admin de Keycloak ya no era la predefinida: en producción la consola es pública,
   por lo que se decidió que el script **pida la contraseña la primera vez** en lugar de usar `admin`/`admin` o una del repositorio.
3. El iToken era fijo (`123456`) y quedaba verificado para siempre en el perfil (`itoken_verified`). Se pidió un
   **código distinto por correo en cada inicio de sesión** (verificación en dos pasos real).

## Resumen de Cambios
### Primer despliegue (C0)
- `deploy-remoto.sh`: opción `--sin-usuarios-prueba`; en el primer despliegue pide (dos veces, sin mostrar) la contraseña del
  admin de Keycloak y la envía por SSH (stdin) a un archivo temporal que se borra al terminar.
- `scripts/generar_env_produccion.py`: `--keycloak-admin-password-file`, `--sin-usuarios-prueba` (`SEED_TEST_USERS=false`)
  y validación de la contraseña (≥ 12 caracteres, sin espacios, comillas, `$` ni `#`).

### iToken por correo
- `authentication/mfa.py` (nuevo): código aleatorio de 6 dígitos (`secrets`), guardado en la sesión solo como hash HMAC-SHA256,
  vence a los 5 minutos, uso único, bloqueo tras 5 intentos, reenvío limitado a uno por minuto, envío con `send_mail`.
- `authentication/views.py`: el callback de Keycloak reinicia `itoken_verified` y envía el código en cada login;
  `mfa_verify_view` valida el código, permite reenviar y audita `MFA_SUCCESS`, `MFA_FAILED` y `MFA_CODE_RESENT`.
- `authentication/middleware.py`: `MFAPendienteMiddleware` impide acceder a otras páginas con el iToken pendiente.
- `mfa_verify.html`: muestra el correo enmascarado, botón "Reenviar" y "Cancelar y cerrar sesión"; se quitó la ayuda `123456`.
- `docs/DESPLIEGUE.md`: primer despliegue, arranque sin usuarios (crear el primer Admin en Keycloak) e iToken por correo.

## Pruebas Unitarias
- `authentication/test_mfa_correo.py` (17): envío en cada login, código correcto, uso único, hash sin código en claro,
  intentos restantes, bloqueo, vencimiento, reenvío y su límite, middleware, logout, usuario sin correo, usuario sin MFA, fallo SMTP.
- `authentication/tests_PSE_4.py`: pruebas de MFA adaptadas al código por correo.
- `scripts/test_generar_env_produccion.py`: +7 pruebas (contraseña elegida/aleatoria/inválida, sin usuarios de prueba, archivo).
- Suite completa: 336 pruebas OK.

## Corrección: correo con Django 5.x (prueba en desarrollo)
- Al probar el iToken en desarrollo no llegaba el correo: `ConnectionRefusedError` a `localhost:25`.
- Causa: la imagen Docker (Python 3.11) instala **Django 5.2**, que ignora `MAILERS` (formato de Django 6.1).
- `globalexchange/email_config.py`: nueva `settings_email_clasicos(mailers)` que traduce `MAILERS` a
  `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `EMAIL_TIMEOUT`;
  `settings.py` la aplica cuando `django.VERSION < (6, 1)`.
- Pruebas: +3 en `globalexchange/test_email_config.py`; `KeycloakBootstrapCommandTests` fija `MAILERS` de consola para no
  depender del `.env` del entorno. Suite completa dentro del contenedor (Django 5.2): 339 pruebas OK. Login SMTP a Gmail verificado.

## Corrección: nota de crédito rechazada ("El operador ESI no tiene permiso para generar DE")
- Al anular una factura fuera del plazo de cancelación, Factura Segura rechazó la nota de crédito con
  "El operador ESI no tiene permiso para generar DE para el RUC 2595733". Factura Segura confirmó por correo que la
  habilitación ESI estaba bien y que los rangos del establecimiento 001 / punto 003 son: facturas 136–147, notas de crédito 49–60.
- Causa: el rango de notas de crédito no estaba configurado (por defecto 1–9999999) y se enviaba `dNumDoc = 0000001`,
  fuera del rango asignado.
- `globalexchange/settings.py` y `procesamiento_operaciones/views.py`: rango por defecto 49–60.
- Base de datos de desarrollo: secuencia `001-003-5` ajustada a `ultimo_numero = 48` (la próxima nota sale con 0000049).
- Pruebas: `RangoNotaCreditoTests` (+2) en `procesamiento_operaciones/test_facturacion_anulacion.py`.

## Anulación de facturas desde la ventanilla del cajero
- Pregunta: ¿el cajero puede anular la factura de un cliente que está en la sucursal? No podía: el rol Cajero no tenía permisos
  de facturación y las solicitudes de operaciones de ventanilla estaban bloqueadas (la devolución automática no aplica al efectivo).
- Decisión: el cajero **solicita** y el Administrador **aprueba** (separación de funciones). Solo se anula la factura en SIFEN;
  los saldos no se modifican y, si se devuelve efectivo, el cajero lo registra como movimiento de efectivo del turno.
- Aclaración de las opciones al aprobar: "Automática" cancela si está dentro del plazo de 48 h (`sol_cancelacion`) y si no emite
  nota de crédito; "Nota de crédito" la fuerza aunque todavía se pueda cancelar.
- `SolicitudAnulacionFactura.con_devolucion` (migración 0014, por defecto True): las solicitudes del cajero van con False y
  `aprobar_solicitud` anula sin devolución.
- `facturacion.solicitar_anulacion_en_caja`: solo operaciones de ventanilla con factura aprobada, sin anulación ni solicitud pendiente.
- `caja`: ruta `ventanilla/solicitar-anulacion/` (permiso `can_operate_cashier`); al buscar al cliente por documento, el POS lista
  sus facturas de ventanilla con el botón "Solicitar anulación" y el estado de la anulación.
- Panel del Administrador: aviso de que la solicitud pedida en ventanilla no devuelve fondos.
- Pruebas: `caja/test_PSE_40_anulacion_caja.py` (8). `caja` + `procesamiento_operaciones`: 134 pruebas OK.

## Turno de caja con saldo inicial cero
- Se quitó la regla que exigía algún saldo inicial mayor a 0 (`caja/views.py`, `abrir_turno_view`): el efectivo de apertura
  se registra como ingreso con desglose de billetes, que el arqueo suma al saldo teórico. Se rechazan saldos negativos.
- `caja/test_PSE_20.py`: la prueba de la regla vieja pasa a `test_can_open_shift_with_zero_balances`.
- `caja/test_PSE_40_turno_saldo_cero.py` (3): apertura en cero, saldo negativo y arqueo CUADRADO con ingreso/salida por desglose.

## Integración de cambios pendientes de la rama `origin/actualizacion`
- La ventanilla ya no mostraba saldos ni verificaba el efectivo disponible: el commit `0723966` (procesamiento de ventanilla
  y detalle de turnos) solo estaba en `origin/actualizacion` y nunca se integró. Se trajo con `cherry-pick -n` y se resolvieron
  los conflictos a mano (diferencias de fin de línea CRLF/LF y la sección de facturas de la ventanilla).
- `caja/models.py`: `calcular_saldos_actuales_turno` (saldo inicial + ingresos − salidas del desglose por divisa).
- `caja/views.py`: saldos por divisa en el POS y en el detalle del turno; en la venta el monto es lo que recibe el cliente
  (p. ej. 2000 USD) y se calcula lo que paga; se bloquea la operación si no hay saldo de la divisa a entregar
  ("Saldo insuficiente de USD en caja …").
- Del commit `64d7d95` la corrección de alertas de tasa ya estaba integrada; faltaba su registro CHIA
  (`docs/prompts/20261018_fix_alerta_tasa_por_cliente.md`).
- Pruebas: `caja/test_PSE_40_saldo_ventanilla.py` (5); `test_ventanilla_factura_sifen.py` adaptada al nuevo cálculo de la venta.
  Suite completa: 357 pruebas OK.

## Tarjetas clickeables en los paneles
- Todas las tarjetas de acceso responden al pasar el mouse (borde azul) y se puede hacer click en toda la tarjeta:
  "Mis Transacciones" del cliente, las 10 del Administrador, las 3 del cajero (la de operaciones del turno solo con turno
  abierto), las del bloque `_accesos_por_permiso.html` (cajero y analista) y "Estado de Cuenta" del panel de usuario.

## Eliminación del panel corporativo
- "Corporativo" es hoy una categoría de beneficio del cliente (como VIP), no una interfaz aparte. Ningún usuario lo usaba.
- Se eliminó `authentication/templates/authentication/corporate_dashboard.html` y su rama en `dashboard_redirect_view`:
  los perfiles corporativos entran al panel de cliente (o al de usuario si no tienen cliente asociado).
- Pruebas: `authentication/test_PSE_40_sin_panel_corporativo.py` (3).
- Relevamiento de lo "corporativo" restante: `CorporateGroup`/`GroupMembership` (grupo Keycloak de cada cliente aprobado),
  los roles Jefe/Operador/Analista (Corporativo) (rol del usuario dentro de su cliente), "Solicitar miembro", el selector de
  grupo, la app `monitoreo_corporativo` (configuración de divisas y métodos de pago) y la categoría CORPORATIVO siguen en uso.
- Se eliminó solo lo que ya no se usa: el rol "Corporate" (migración `authentication/0024_eliminar_rol_corporate`: los perfiles
  con ese rol pasan a "Cliente") y la regla del login con Keycloak que lo asignaba y marcaba `is_corporate` a quien tuviera
  roles "corporate", "corporativo", "empresa" o "jefe". `seed_data` ya no crea el rol.
- Pruebas: +2 en `authentication/test_PSE_40_sin_panel_corporativo.py`; `tests_PSE_4.py` y `gestion_clientes/test_PSE_7.py` ya
  no crean el rol "Corporate". Suite completa: 362 pruebas OK.
