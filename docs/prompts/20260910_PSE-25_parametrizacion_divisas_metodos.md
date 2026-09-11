# Registro de Conversación IA (CHIA) - Historia de Usuario PSE-25

**Fecha:** 10 de Septiembre de 2026  
**Historia de Usuario:** PSE-25 - Parametrización y CRUD de Divisas y Métodos de Pago  
**Asistente:** OpenCode (gemini-3.5-flash-lite)  
**Proyecto:** Global Exchange (Ingeniería de Software 2 - FPUNA)  

---

## 1. Contexto y Objetivos
Como Administrador del Sistema, se requería implementar el **CRUD completo (Crear, Leer, Actualizar y Eliminar)** tanto para **Monedas (Divisas)** como para **Métodos de Pago / Medios de Cobro de Clientes**, garantizando:
1. Registro, edición y eliminación de divisas indicando nombre, código ISO, símbolo cambiario y tasas de compra/venta.
2. Gestión CRUD y activación/desactivación dinámica (toggles) de métodos de pago (tarjeta, transferencia, billeteras electrónicas, etc.) con persistencia en base de datos.
3. Restricción estricta de permisos de acceso exclusivo para administradores.
4. Integración en el panel administrativo (`/auth/dashboard/`) mediante tarjeta dedicada.

---

## 2. Implementación Realizada

### A. Modelos de Datos (`tasas_cambio/models.py`)
- `ExchangeRate`: Incluye código ISO, nombre, símbolo (`$`, `€`, `R$`, `₲`), tasas de compra y venta.
- `PaymentMethod`: Modelo completo para medios de pago con código, nombre, descripción y estado booleano `is_active`.

### B. Vistas y Lógica CRUD (`tasas_cambio/views.py`)
- Se implementó `currency_payment_config_view` con soporte completo para:
  - `add_currency`: Alta y actualización de divisas comerciales.
  - `delete_currency`: Eliminación controlada de divisas (protegiendo la divisa base PYG).
  - `add_payment_method`: Creación y actualización de medios de cobro.
  - `toggle_payment_method`: Activación / desactivación en tiempo real.
  - `delete_payment_method`: Eliminación de métodos de pago.
- Verificación de permisos de Administrador (`is_superuser` o rol `Admin`).

### C. Interfaz Web ("Corporate Modern") (`currency_payment_config.html` & `admin_dashboard.html`)
- Se diseñó un panel de administración con diseño Corporate Modern utilizando Tailwind CSS.
- Se añadieron formularios dinámicos con modo de edición y limpieza para ambos catálogos.
- Se integró la tarjeta de acceso directo en el panel administrativo principal (`/auth/dashboard/`).

### D. Pruebas Unitarias Exhaustivas (`tasas_cambio/test_PSE_25.py`)
- Suite `ParametrizacionDivisasMetodosPSE25Tests` que valida:
  1. `test_currency_crud_operations`: Ciclo CRUD completo de divisas.
  2. `test_payment_method_crud_and_toggle`: Ciclo CRUD y toggles de métodos de pago.
  3. `test_admin_permission_restriction`: Restricción y redirección de usuarios no administradores.
  4. `test_admin_post_crud_integration`: Integración web POST de las acciones CRUD.

---

## 3. Evidencia de Verificación y Pruebas
- Comando ejecutado:
  ```bash
  python manage.py test
  ```
- Resultado:
  ```
  Ran 50 tests in 109.311s
  OK
  ```
- La totalidad de pruebas del sistema pasaron exitosamente.
