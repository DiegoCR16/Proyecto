# Registro de Conversación y Desarrollo (CHIA) - PSE-2: Registro de Clientes (Personas Físicas y Jurídicas) e Integración Keycloak Admin REST API

- **Fecha:** 29-30 de Agosto de 2026
- **Historia de Usuario:** PSE-2 (Registro de Clientes, Selección Física/Jurídica, Integración Admin REST API Keycloak)
- **Rama Git:** `feature/PSE-2`
- **Asignatura:** Ingeniería de Software 2 - FPUNA

## Resumen de Implementación
1. **Modelos y Datos (`models.py`):**
   - Incorporación del campo `ci_ruc` en el modelo `UserProfile` con restricción de unicidad para garantizar el control de duplicados de clientes.
   - Inclusión de docstrings en formato Google/Sphinx en todas las clases y funciones del módulo de autenticación.
2. **Vistas, Endpoints y Admin REST API de Keycloak (`views.py` & `urls.py`):**
   - Creación de la vista `register_view` con validaciones robustas (campos obligatorios, nombre alfabético, número de cédula/RUC válido con soporte para guiones, correo electrónico con máscara `texto@dominio.extensión`, y contraseña segura de mínimo 8 caracteres con mayúsculas, minúsculas y caracteres especiales).
   - Control de duplicados consultando existencia previa tanto en la base de datos local como en la **Admin REST API de Keycloak** (`/admin/realms/{realm}/users?email={email}`).
   - Integración en segundo plano con la **Admin REST API de Keycloak** (solicitud de token admin con `client_credentials` y creación de usuario mediante `/admin/realms/{realm}/users` enviando atributos `userType: ["fisica"]`, `ci_ruc`, y `requiredActions: ["VERIFY_EMAIL"]` para validación de correo con Mailpit).
   - Registro de eventos en `AuditLog` ante intentos de registro duplicado, errores de API y registros exitosos (`REGISTER_SUCCESS`).
3. **Interfaz Gráfica / Plantilla ("Corporate Modern"):**
   - Actualización de la plantilla `register.html` con un selector interactivo inicial para elegir entre **Persona Física** y **Persona Jurídica** (con aviso informativo para Jurídica).
   - Estilizada con Tailwind CSS aplicando la paleta de colores corporativa y controles responsivos.
   - Conexión del botón "Registrarse" desde la vista de login hacia el formulario integrado.
4. **Pruebas Unitarias (PUN):**
   - Actualización y ejecución exitosa de la suite de pruebas unitarias (`authentication/test_pse2.py`) empleando `unittest.mock` para validar la lógica de registro, validaciones, control de duplicados y llamadas mockeadas a la Admin REST API de Keycloak (17 pruebas exitosas).
5. **Control de Versiones (Git Flow):**
   - Trabajo continuo en la rama de funcionalidad `feature/PSE-2`.

## Criterios Probados
1. **Selector de Tipo de Persona:** Verificación de visualización del formulario físico o aviso jurídico según la selección.
2. **Validación de Campos Obligatorios y Formatos:** Comprobación de rechazo ante campos vacíos, nombres inválidos, cédulas/RUCs con formato incorrecto, correos sin máscara válida y contraseñas débiles.
3. **Control de Duplicados (Local y Keycloak):** Denegación de registro y registro en `AuditLog` si el correo electrónico o número de cédula/RUC ya existe.
4. **Integración con Keycloak Admin REST API:** Creación exitosa en segundo plano enviando atributos de tipo de usuario y acción de verificación de correo.

## Comandos Utilizados
- Creación y aplicación de migraciones de base de datos:
  ```bash
  python manage.py makemigrations authentication
  python manage.py migrate
  ```
- Ejecución de la suite de pruebas unitarias:
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
.................Found 17 test(s).
System check identified no issues (0 silenced).

----------------------------------------------------------------------
Ran 17 tests in 24.443s

OK
Destroying test database for alias 'default'...
