# Registro de Conversación IA - PSE-24: Monitoreo y Dashboard de Ganancias Cambiarias

- **Fecha:** 25/10/2026 (implementado el 17/11/2026)
- **Épica:** PSE-23 - Monitoreo Corporativo, Ganancias y Parametrización
- **Rama:** `feature/PSE-24` (creada desde `feature/PSE-41`, a pedido del usuario, porque reutiliza su servicio de reportes)
- **Asistente:** Claude Code (Claude Opus 5.5)

## Historia de Usuario
Como Analista Cambiario / Administrador, quiero acceder a un panel en tiempo real y a gráficos de ganancias
mensuales por divisa, para evaluar la rentabilidad del negocio y tomar decisiones financieras informadas.

## Análisis previo
- **Gráficos:** el proyecto ya usa Chart.js por CDN (`tasas_cambio/templates/tasas_cambio/rates_evolution.html`)
  con una API JSON y `fetch()`. Se reutilizó el mismo enfoque, sin dependencias nuevas.
- **Tiempo real:** no hay WebSockets ni Django Channels. Se implementó polling: la página consulta la API JSON
  cada 30 segundos.
- **Cálculo:** se reutilizó `monitoreo_corporativo/reportes.py` (PSE-41). Así el dashboard y el reporte
  financiero dan siempre los mismos números.

## Decisiones acordadas con el usuario
- **Permiso:** se reutiliza `can_view_financial_reports` (Admin y Analista), el mismo del reporte de PSE-41.
- **Operaciones cruzadas** (sin PYG, ej. USD→EUR): la ganancia se atribuye a la divisa de origen.
- **Ganancia neta** = comisión − IVA en PYG, solo operaciones `SUCCESS` (criterio heredado de PSE-41).

## Criterios de aceptación implementados
1. **Dashboard en tiempo real:**
   - KPIs del día y del mes: ganancia neta, volumen, comisiones y cantidad de operaciones.
   - Tabla de cotizaciones activas (compra y venta).
   - Se actualiza por polling a `/monitoreo/api/dashboard-ganancias/`, con un indicador "En vivo" y la hora de la
     última actualización. El indicador se pone en rojo si falla la consulta.
2. **Gráfico de barras mensual:** Chart.js, una serie (barra) por divisa y un grupo por mes. Período de 6 o 12
   meses, con tooltips y eje en ₲.
3. **Moneda base PYG obligatoria:** `dashboard.MONEDA_BASE = 'PYG'`. Todos los importes se consolidan en Guaraníes
   (las comisiones guardadas en otra moneda se convierten con `reportes.factor_comision_a_pyg`). La API expone
   `moneda_base: "PYG"` y la vista lo indica en el encabezado y en el gráfico.

## Archivos
| Archivo | Cambio |
|---|---|
| `monitoreo_corporativo/dashboard.py` | Nuevo: KPIs, cotizaciones activas, ganancias mensuales por divisa y serialización JSON |
| `monitoreo_corporativo/views.py` | `dashboard_ganancias_view` y `dashboard_ganancias_api` |
| `monitoreo_corporativo/urls.py` | `/monitoreo/dashboard-ganancias/` y `/monitoreo/api/dashboard-ganancias/` |
| `monitoreo_corporativo/templates/monitoreo_corporativo/dashboard_ganancias.html` | Nuevo: dashboard "Corporate Modern" con Chart.js y polling |
| `monitoreo_corporativo/test_PSE_24_dashboard_ganancias.py` | Nuevo: suite independiente (24 tests) |
| `authentication/templates/authentication/admin_dashboard.html` | Tarjeta "Dashboard de Ganancias" |
| `authentication/templates/authentication/_accesos_por_permiso.html` | Tarjeta para roles con el permiso (Analista) |

No se modificó ninguna prueba existente ni `requirements.txt`.

## Comandos ejecutados
```bash
git checkout -b feature/PSE-24        # desde feature/PSE-41
docker compose run --rm --no-deps -e DB_ENGINE=django.db.backends.sqlite3 web \
    python manage.py test monitoreo_corporativo.test_PSE_24_dashboard_ganancias
docker compose run --rm --no-deps -e DB_ENGINE=django.db.backends.sqlite3 web python manage.py test
# Verificación adicional contra PostgreSQL 15 (contenedor temporal en una red aislada)
docker run --rm --network pse24-test ... proyecto-web python manage.py test \
    monitoreo_corporativo.test_PSE_24_dashboard_ganancias monitoreo_corporativo.test_PSE_41_reportes_financieros
node --check dash.js                  # sintaxis del JavaScript del dashboard
```

## Evidencia de pruebas
Suite PSE-24:
```
Ran 24 tests in 5.374s
OK
```
Suite completa del proyecto (pruebas existentes + PSE-41 + PSE-24):
```
Ran 426 tests in 214.293s
OK
```
PostgreSQL 15 (PSE-24 + PSE-41):
```
Ran 56 tests in 30.112s
OK
```
Los únicos avisos son los `models.W042` (`DEFAULT_AUTO_FIELD`), que ya existían.

Cobertura de la suite PSE-24:
- `GraficoMensualPorDivisaTests`:
  - Cálculo de los meses (incluido el cambio de año) y etiquetas.
  - Divisa de la operación (incluidas las cruzadas).
  - Series por divisa a 6 y 12 meses y totales por mes.
  - Exclusión de las operaciones FAILED y período sin datos.
- `KPIsConsolidadosPYGTests`:
  - KPIs del día y del mes en PYG.
  - Coincidencia con el reporte de PSE-41.
  - Moneda base PYG, período inválido y serialización JSON.
- `CotizacionesActivasTests`: cotizaciones en PYG, sin incluir la moneda base.
- `DashboardGananciasVistaTests`:
  - Render del dashboard con Chart.js, datos iniciales y polling.
  - Acceso de Admin y Analista; denegación a Cliente y anónimo.
  - Estructura JSON de la API.
  - **Integración en tiempo real:** una operación nueva y un cambio de tasa aparecen en la siguiente consulta.
  - Parámetro `meses` y accesos en los dashboards.
