# Registro de Conversación IA (CHIA) - Permisos por rol funcionando (RBAC)

**Fecha:** 11 de Noviembre de 2026  
**Rama:** `actualizacion` (creada desde `main`)  
**Asistente:** Claude Code (claude-opus-5-5)  

---

## 1. Resumen de la Solicitud
> "Los permisos que tiene cada interfaz también falta configurar... en administrador tenemos la configuración de los permisos y falta que funcione eso: qué pueden hacer los clientes, los cajeros, los analistas, los clientes con rol operador y los clientes con rol analista. En administrador hay una tarjeta para simular divisas y eso no hace falta que el administrador lo vea."

---

## 2. Diagnóstico
- La pantalla "Gestión de Permisos para Roles" guardaba los permisos, pero **ninguna vista los consultaba**. Cada vista comparaba el nombre del rol a mano (`'admin' in role.name`), con criterios distintos entre pantallas.
- Solo existían 6 permisos, que no cubrían caja, historial, alertas, simulador ni medios de acreditación.
- Algunas incoherencias: el panel del Analista enlazaba a Auditoría pero la vista solo dejaba entrar al Admin. El Gestor de Cotizaciones dejaba entrar a cualquier rol llamado "operador" o "cajero". El POS de ventanilla no exigía sesión iniciada.

---

## 3. Desarrollo e Implementación
- **Módulo `authentication/permisos.py`:**
  - Catálogo de 15 permisos: 9 administrativos y 6 de cliente.
  - Permisos por defecto de cada rol.
  - `rol_efectivo` determina con qué rol opera el usuario:
    - Admin.
    - Cajero, por rol o porque está asignado a una caja.
    - Analista del sistema.
    - Rol dentro del cliente activo: Jefe, Operador o Analista corporativo.
    - Cliente.
  - `permisos_de`, `tiene_permiso` y el decorador `requiere_permiso`. Si falta el permiso, se registra `RBAC_ACCESS_DENIED` en la auditoría y el usuario vuelve a su panel con la notificación *"Acceso denegado: tu rol (X) no tiene el permiso «Y»"*.
  - El context processor `contexto_permisos` expone `permisos` a las plantillas.
- **`Role.permisos_configurados`** (migración 0022): mientras el administrador no guarde un rol, este usa sus permisos por defecto. Al abrir la pantalla de roles, los roles sin configurar muestran y guardan sus permisos por defecto.
- **Pantalla de roles:**
  - Usa el catálogo completo.
  - Los roles de cliente (Cliente, Jefe, Operador, Analista corporativo) no pueden recibir permisos administrativos.
  - Nadie que no sea Admin puede modificar su propio rol.
  - Confirma cada cambio con una notificación.
- **Vistas protegidas por permiso:**
  - Compra y venta, los 3 historiales y medios de acreditación.
  - Simulador, alertas, pizarra y evolución. Las páginas públicas siguen abiertas para visitantes sin sesión.
  - Turnos, movimientos, detalle de turno, ventanilla y procesar ventanilla.
  - Gestión de cajas, beneficios, divisas y métodos de pago, y gestor de cotizaciones.
  - Usuarios, clientes, aprobación de solicitudes, roles y auditoría.
- **Paneles:** cada tarjeta se muestra solo si el rol tiene el permiso. El panel del Analista y el del Cajero muestran las tarjetas de los módulos que el Admin les delegue (`_accesos_por_permiso.html`). Se quitó la tarjeta "Simulación de Tasas" del panel de administrador.

### Permisos por defecto
| Rol | Permisos |
|---|---|
| Admin | Todos (protegido) |
| Analista | Ver Auditoría, Simular, Ver Pizarra |
| Cajero | Operar Caja y Ventanilla, Ver Pizarra |
| Cliente / Jefe (Corporativo) | Transacciones, Historial, Alertas, Medios de Acreditación, Simular, Ver Pizarra |
| Operador (Corporativo) | Transacciones, Historial, Alertas, Simular, Ver Pizarra |
| Analista (Corporativo) | Historial, Alertas, Simular, Ver Pizarra |

- **Pruebas:** `authentication/test_permisos_roles.py` (16 pruebas). Se ajustaron 2 pruebas existentes (`test_PSE_30` y `test_PSE_25`) porque un acceso denegado ahora vuelve al panel con notificación en lugar de ir a la pizarra pública.

---

## 4. Comandos Ejecutados
```bash
python manage.py makemigrations --check --dry-run
python manage.py test authentication.test_permisos_roles
python manage.py test
```

---

## 5. Evidencia de Pruebas Exitosas
```text
Ran 226 tests
OK
```
