# Prompt CHIA: Independencia de Apertura/Cierre de Caja por Cajero y Restricción de Notificaciones de Descuadre al Administrador

## Fecha: 9 de Octubre, 2026

## Requerimiento del Usuario
- El tema de apertura y cierre de caja de los usuarios con rol cajero debe ser independiente para cada cajero.
- El administrador debe poder visualizar todas las cajas activas.
- Las notificaciones de descuadres de la caja (`BitacoraArqueo` / reportes de arqueo) solo deben poder ser visualizadas por el administrador, no por los usuarios con rol cajero.

## Cambios Implementados
1. **Independencia por Cajero**:
   - En `gestion_caja_view` (`caja/views.py`), las cajas mostradas a los usuarios no administradores (`cajeros`) se filtran excluyendo aquellas que posean un turno activo abierto por otro cajero. Esto asegura que cada cajero gestione su propio turno y caja de forma independiente.
2. **Visualización de Cajas Activas por el Administrador**:
   - El administrador (`is_admin = True`) visualiza todas las cajas activas (`Caja.objects.filter(activa=True)`) y turnos en el sistema.
3. **Restricción de Notificaciones de Descuadre (Arqueos / Bitácora)**:
   - Los datos de turnos cerrados (`turnos_cerrados`) y registros en la bitácora de arqueo (`bitacoras_recientes`) solo se cargan en la vista si el usuario es administrador (`is_admin`).
   - En la plantilla `caja/templates/caja/gestion_caja.html`, las secciones de reporte comparativo de arqueo y bitácora de auditoría/alertas están protegidas con `{% if is_admin %}`, ocultándolas por completo a los cajeros.
4. **Pruebas Unitarias**:
   - Se añadió un test unitario (`test_cashier_independent_and_no_discrepancy_notifications_visibility`) en `caja/test_PSE_20.py` que valida la restricción de visibilidad de notificaciones para cajeros y la visibilidad completa para el administrador.
