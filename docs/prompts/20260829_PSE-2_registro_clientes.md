# Registro de Conversación y Desarrollo (CHIA) - PSE-2: Registro de Clientes Personas Físicas

- **Fecha:** 29 de Agosto de 2026
- **Historia de Usuario:** PSE-2 (Registro de Clientes Personas Físicas)
- **Rama Git:** `feature/PSE-2`
- **Asignatura:** Ingeniería de Software 2 - FPUNA

## Resumen de Implementación
1. **Modelos y Datos (`models.py`):**
   - Incorporación del campo `ci_ruc` en el modelo `UserProfile` con restricción de unicidad para garantizar el control de duplicados de clientes.
   - Inclusión de docstrings en formato Google/Sphinx en todas las clases y funciones del módulo de autenticación (PDO).
2. **Vistas y Endpoints de Registro (`views.py` & `urls.py`):**
   - Creación de la vista `register_view` para el registro de Personas Físicas con validaciones robustas (campos obligatorios, nombre alfabético, número de cédula/RUC válido, correo electrónico con máscara `texto@dominio.extensión`, y contraseña segura de mínimo 8 caracteres con mayúsculas, minúsculas y caracteres especiales).
   - Control de duplicados consultando existencia previa de correo electrónico en `User` y cédula/RUC en `UserProfile`.
   - Registro de eventos en `AuditLog` ante intentos de registro duplicado y validaciones exitosas.
   - Creación del endpoint `keycloak_register_redirect` para redireccionar el flujo de captura de datos y credenciales a Keycloak IdP (`/protocol/openid-connect/registrations`).
3. **Interfaz Gráfica / Plantilla ("Corporate Modern"):**
   - Creación de la plantilla `register.html` estilizada con Tailwind CSS aplicando el fondo neutro (`slate-50`), tarjeta central con sombras profesionales, paleta de colores corporativa (`slate-900`, `blue-800`, `blue-600`, `red-600`), inputs interactivos con focus azul y mensajes de error destacados.
   - Conexión del botón "Registrarse" desde la vista de login hacia el formulario de registro PSE-2.
4. **Pruebas Unitarias (PUN):**
   - Creación y ejecución exitosa de una nueva suite de pruebas unitarias independiente (`authentication/test_pse2.py`) separada de las pruebas existentes, destinada exclusivamente a validar la lógica de registro, validaciones de campos y manejo de respuestas/excepciones.
5. **Control de Versiones (Git Flow):**
   - Creación y posicionamiento en la rama de funcionalidad `feature/PSE-2`.

## Criterios Probados
1. **Redirección a IdP / Keycloak Registrations:** Verificación de que el acceso a `/auth/register/sso/` redirige correctamente al endpoint de registros OIDC de Keycloak.
2. **Validación de Campos Obligatorios:** Comprobación de rechazo cuando faltan campos requeridos.
3. **Validación de Nombre Completo:** Rechazo de nombres que contienen números o símbolos no alfabéticos.
4. **Validación de Cédula / RUC:** Comprobación de formato numérico válido.
5. **Validación de Correo Electrónico:** Verificación de cumplimiento de la máscara `texto@dominio.extensión`.
6. **Validación de Seguridad de Contraseña:** Rechazo de contraseñas menores a 8 caracteres o faltantes de mayúsculas, minúsculas o caracteres especiales.
7. **Control de Duplicados:** Denegación de registro y registro en `AuditLog` si el correo electrónico o número de cédula/RUC ya existe en el sistema.
8. **Redirección por Validación Exitosa:** Verificación de que un registro válido redirige al flujo de Keycloak con auditoría exitosa registrada.

## Comandos Utilizados
- Creación de rama de funcionalidad:
  ```bash
  git checkout -b feature/PSE-2
  ```
- Creación y aplicación de migraciones de base de datos:
  ```bash
  python manage.py makemigrations authentication
  python manage.py migrate
  ```
- Ejecución de la suite de pruebas unitarias independiente para PSE-2:
  ```bash
  python manage.py test authentication.test_pse2
  ```
- Ejecución de todas las pruebas del proyecto:
  ```bash
  python manage.py test
  ```

## Evidencia de las Pruebas Exitosas
```text
Creating test database for alias 'default'...
.........Found 9 test(s).
System check identified no issues (0 silenced).

----------------------------------------------------------------------
Ran 9 tests in 6.398s

OK
Destroying test database for alias 'default'...
```
