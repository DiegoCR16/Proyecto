# Registro de Conversación IA - PSE-41: Generación y Exportación de Reportes de Transacciones y Ganancias

- **Fecha:** 25/10/2026 (implementado el 17/11/2026)
- **Épica:** PSE-23 - Monitoreo Corporativo, Ganancias y Parametrización
- **Rama:** `feature/PSE-41` (creada desde `main`)
- **Asistente:** Claude Code (Claude Opus 5.5)

## Historia de Usuario
Como Administrador o Analista Financiero, quiero generar y exportar reportes detallados del historial de
transacciones operativas y las ganancias cambiarias en un rango de fechas, para presentar informes contables
formales y realizar auditorías financieras de la entidad.

## Decisiones acordadas con el usuario
- **Ganancia neta = comisión − IVA de la comisión**, en PYG. Es el único ingreso que el sistema registra
  (`commission_amount` y `tax_amount`).
- **Solo operaciones `SUCCESS`.** Se excluyen las `FAILED`, `PENDING`, `CANCELLED` y `ANULADA` (factura anulada
  con devolución de fondos).
- **Volumen operado** = `total_pyg` de cada operación.
- **Exportación sin dependencias nuevas**, igual que la planilla de cierre de caja (`caja/views.py`):
  - **Excel/CSV:** CSV generado con el módulo estándar `csv` (separador `;` y BOM UTF-8 para que Excel lo abra
    con las columnas separadas).
  - **PDF:** vista de impresión HTML con `@media print` y botón `window.print()`; se guarda con «Guardar como PDF»
    del navegador.
  - En una primera versión se habían agregado `openpyxl` y `reportlab`. El usuario señaló que el proyecto ya
    resolvía la exportación sin instalar nada, así que se quitaron y se siguió esa convención.

## Hallazgo durante el análisis
La comisión no siempre está guardada en Guaraníes, aunque el campo se llame "Comisión (Gs)":
- **Online:** se calcula sobre el monto convertido, o sea en la moneda de destino. Si el destino no es PYG,
  se convierte con `total_pyg / converted_amount`.
- **Ventanilla (`EFECTIVO_VENTANILLA`):** queda en la moneda de origen. Si el origen no es PYG, se convierte
  con la tasa aplicada.

Esta conversión está en `reportes.factor_comision_a_pyg()` y tiene tests.

## Criterios de aceptación implementados
1. **Filtros combinables:** rango de fechas (inclusivo, día completo en hora de Asunción), tipo (Compra/Venta),
   moneda (coincide con la de origen o la de destino), cajero (cajero del turno de caja) y cliente.
   Se valida que "Desde" no sea posterior a "Hasta".
2. **Exportación:** Excel/CSV (`?formato=csv`, descarga) y PDF (`?formato=pdf`, vista de impresión A4 apaisada
   que se abre en otra pestaña), consolidados en PYG y con los mismos filtros de la vista.
3. **Resumen de totales:** cantidad de operaciones, volumen de compras, de ventas y total, comisiones, IVA y
   ganancia neta. Aparece en el `<tfoot>` y en las tarjetas de la vista web, al pie del CSV y en el `<tfoot>` y el
   bloque "Resumen de Totales" de la vista de impresión.

## Archivos
| Archivo | Cambio |
|---|---|
| `monitoreo_corporativo/reportes.py` | Nuevo: servicio de filtros, consolidación PYG, totales y exportador CSV |
| `monitoreo_corporativo/views.py` | Nuevo: `FiltroReporteForm` y `reporte_financiero_view` |
| `monitoreo_corporativo/urls.py` | Nuevo: `/monitoreo/reportes-financieros/` |
| `monitoreo_corporativo/templates/monitoreo_corporativo/reporte_financiero.html` | Nuevo: panel "Corporate Modern" |
| `monitoreo_corporativo/templates/monitoreo_corporativo/reporte_financiero_imprimir.html` | Nuevo: vista de impresión / PDF |
| `monitoreo_corporativo/test_PSE_41_reportes_financieros.py` | Nuevo: suite independiente (32 tests) |
| `globalexchange/urls.py` | Incluye las URLs de `monitoreo_corporativo` |
| `authentication/permisos.py` | Nuevo permiso `can_view_financial_reports` (por defecto: Admin y Analista) |
| `authentication/templates/authentication/admin_dashboard.html` | Tarjeta "Reportes Financieros" |
| `authentication/templates/authentication/_accesos_por_permiso.html` | Tarjeta para roles con el permiso (Analista) |

No se modificó ninguna prueba existente.

## Comandos ejecutados
```bash
git checkout -b feature/PSE-41
docker compose build web
docker compose run --rm --no-deps -e DB_ENGINE=django.db.backends.sqlite3 web \
    python manage.py test monitoreo_corporativo.test_PSE_41_reportes_financieros -v 2
docker compose run --rm --no-deps -e DB_ENGINE=django.db.backends.sqlite3 web python manage.py test
# Verificación adicional contra PostgreSQL 15 (contenedor temporal en una red aislada)
docker run --rm --network pse41-test ... proyecto-web python manage.py test monitoreo_corporativo.test_PSE_41_reportes_financieros
```

## Evidencia de pruebas
Suite PSE-41 (SQLite y PostgreSQL 15):
```
Ran 32 tests in 23.939s
OK
```
Suite completa del proyecto (pruebas existentes + PSE-41):
```
Ran 402 tests in 208.333s
OK
```
Los únicos avisos son los `models.W042` (`DEFAULT_AUTO_FIELD`), que ya existían.

Cobertura de la suite PSE-41:
- `ConsolidacionPYGTests`: conversión de comisiones a PYG, ganancia neta, operador (cajero o usuario),
  totales sin filtros excluyendo FAILED/PENDING/ANULADA, orden y totales vacíos.
- `FiltrosReporteTests`: fechas inclusivas, tipo, moneda (origen o destino), cajero, cliente, combinación de
  filtros, sin resultados y descripción de los filtros.
- `ExportacionArchivosTests`: estructura, importes por operación y totales del CSV (con y sin operaciones).
- `VistaReporteFinancieroTests`: acceso de Admin y Analista, denegación a Cliente y anónimo, filtros por GET,
  rango inválido, descarga CSV y vista de impresión (`window.print()`, `@media print`, totales) con los filtros
  aplicados, formato desconocido, exportaciones bloqueadas sin permiso y tarjeta en el dashboard.

## Nota para roles ya configurados
Si en "Gestión de Permisos para Roles" ya se guardaron los permisos del rol Analista, el permiso nuevo no se
le agrega solo: el Administrador tiene que marcar "Ver Reportes Financieros" para ese rol.
