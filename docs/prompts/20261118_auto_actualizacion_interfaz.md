# Registro de Conversación IA - Actualización automática de las interfaces (cada 30 segundos)

- **Fecha:** 18/11/2026
- **Rama:** `feature/PSE-24` (a pedido del usuario, junto con el dashboard de ganancias)
- **Asistente:** Claude Code (Claude Opus 5.5)

## Pedido del usuario
> "yo quiero que la interfaz del administrador y del cliente se actualicen cada 30 segundos ya que si se les
> envía alguna notificación manualmente hay que actualizar para ver en la interfaz."

Se acordó aplicarlo también al dashboard del Analista; el del Cajero no.

## Análisis previo
- **Cliente:** sus notificaciones son registros de `NotificationLog` (alertas de tasa, variaciones abruptas,
  facturación) y solo se veían al cargar el dashboard.
- **Administrador:** su aviso es el contador de solicitudes de anulación pendientes de la tarjeta
  "Facturación Electrónica".
- **Por qué no se recarga a ciegas cada 30 s:** el dashboard del cliente tiene formularios (selector de cliente,
  inputs), y una recarga ciega borraría lo que el usuario esté escribiendo.

## Solución implementada
Se hace polling con recarga solo cuando hay cambios, el mismo patrón de polling del dashboard de PSE-24:
1. Cada 30 s la página consulta `GET /auth/api/estado-interfaz/`. El endpoint devuelve una **firma** del estado:
   - Notificaciones visibles sin leer y el id de la más reciente.
   - Solicitudes de anulación pendientes y la más reciente (solo con el permiso `can_manage_invoices`).
   - Última actualización de las cotizaciones.
2. Si la firma cambió, la página se recarga y muestra la novedad.
3. Si el usuario está escribiendo en un campo, la recarga espera a que salga del campo.
4. Con la pestaña oculta no se consulta; al volver a la pestaña se consulta enseguida.
5. La firma inicial se calcula con la misma función al renderizar el dashboard. Así la comparación siempre es
   coherente y no hay recargas en bucle. Si ese cálculo falla, el dashboard se muestra igual, sin
   actualización automática.

## Archivos
| Archivo | Cambio |
|---|---|
| `authentication/estado_interfaz.py` | Nuevo: `estado_interfaz(request)` y la firma del estado |
| `authentication/views.py` | `estado_interfaz_api`; `dashboard_redirect_view` agrega `estado_interfaz` e `intervalo_auto_actualizacion` al contexto |
| `authentication/urls.py` | Ruta `api/estado-interfaz/` |
| `authentication/templates/authentication/_auto_actualizacion.html` | Nuevo: parcial con el script de polling |
| `admin_dashboard.html`, `client_dashboard.html`, `user_dashboard.html`, `analista_dashboard.html` | Incluyen el parcial |
| `authentication/test_auto_actualizacion_interfaz.py` | Nuevo: suite independiente (15 tests) |

No se modificó ninguna prueba existente ni `requirements.txt`.

## Comandos ejecutados
```bash
docker compose run --rm --no-deps -e DB_ENGINE=django.db.backends.sqlite3 web \
    python manage.py test authentication.test_auto_actualizacion_interfaz
docker compose run --rm --no-deps -e DB_ENGINE=django.db.backends.sqlite3 web python manage.py test
# PostgreSQL 15 temporal en red aislada
docker run --rm --network auto-test ... proyecto-web python manage.py test authentication.test_auto_actualizacion_interfaz
node --check auto.js   # sintaxis del JavaScript del parcial
```

## Evidencia de pruebas
Suite nueva (SQLite y PostgreSQL 15):
```
Ran 15 tests in 11.318s
OK
```
Suite completa del proyecto:
```
Ran 441 tests in 224.943s
OK
```

Cobertura:
- **API:** requiere sesión y la firma es estable si no hay cambios. La firma cambia con:
  - Una notificación del cliente, una personal o una general.
  - Una notificación marcada como leída.
  - Una solicitud de anulación (solo para el Admin).
  - Una cotización actualizada.
- **Aislamiento:** una notificación de otro cliente no afecta la firma, y el cliente no ve las solicitudes de
  anulación.
- **Dashboards:** los del Admin, el Cliente, el modo usuario y el Analista incluyen la firma inicial y el
  polling; el del Cajero no. La firma inicial coincide con la de la API.
