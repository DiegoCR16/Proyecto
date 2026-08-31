# Registro de Conversación y Desarrollo (CHIA) - PSE-3: Registro de Clientes Personas Jurídicas

- **Fecha:** 30 de Agosto de 2026
- **Historia de Usuario:** PSE-3 (Registro de Clientes Personas Jurídicas, Delegación a Keycloak, validaciones de empresa, RUC, correo y contraseña)
- **Rama Git:** `feature/PSE-3`
- **Asignatura:** Ingeniería de Software 2 - FPUNA

## Resumen de Implementación
1. **Frontend y Plantilla (`register.html`):**
   - Implementación del formulario interactivo para Persona Jurídica en sustitución del aviso previo, solicitando Nombre de la Empresa, RUC de la Empresa, Correo Electrónico Corporativo y Contraseña Corporativa.
   - Aplicación de los lineamientos de diseño global ("Corporate Modern") con Tailwind CSS.
2. **Backend y Vistas (`views.py`):**
   - Actualización de `register_view` para procesar peticiones POST de Persona Jurídica (`person_type == 'juridica'`).
   - Validaciones estrictas: campos obligatorios, nombre de la empresa con caracteres alfabéticos y puntos (para denominaciones legales como S.A., S.R.L.), formato numérico válido de RUC, máscara de correo electrónico corporativo (`texto@dominio.extensión`) y contraseña segura (mínimo 8 caracteres, mayúsculas, minúsculas y caracteres especiales).
   - Control de duplicados tanto localmente en la base de datos (`User` y `UserProfile` por correo y RUC) como en la **Admin REST API de Keycloak**.
   - Integración con Keycloak Admin REST API en segundo plano para crear el usuario con atributos `userType: ["juridica"]`, `ci_ruc`, y `requiredActions: ["VERIFY_EMAIL"]`.
   - Asignación automática del rol `"Corporate"` e indicador `is_corporate = True` en el perfil de usuario.
   - Registro de auditoría en `AuditLog` para intentos duplicados, errores de API y registros exitosos (`REGISTER_SUCCESS`).
3. **Pruebas Unitarias Exclusivas (PUN):**
   - Creación de un archivo de pruebas unitarias nuevo e independiente: `authentication/test_pse3_registro_juridico.py`, conteniendo 8 pruebas exhaustivas para validar carga de página, validaciones de datos y campos, control de duplicados e integración mockeada con la Admin REST API de Keycloak.
4. **Calidad y Documentación (PDO):**
   - Inclusión de docstrings en formato Google/Sphinx en clases y funciones nuevas y modificadas.

## Criterios Probados
1. **Delegación a Keycloak:** Verificación de llamada a Admin REST API con atributos corporativos y verificación nativa de correo.
2. **Campos Requeridos:** Solicitud validada de nombre de empresa, RUC, correo corporativo y contraseña.
3. **Validación de Datos y Seguridad:** Comprobación de rechazo ante nombres inválidos, formatos RUC incorrectos, correos sin máscara válida y contraseñas débiles.
4. **Control de Duplicados:** Denegación y registro de auditoría si el correo electrónico o RUC ya se encuentra registrado.

## Comandos Utilizados
- Ejecución de pruebas unitarias de la historia PSE-3:
  ```bash
  python manage.py test authentication.test_pse3_registro_juridico
  ```
- Ejecución de toda la suite de pruebas del proyecto:
  ```bash
  python manage.py test
  ```

## Evidencia de las Pruebas Exitosas
```text
Creating test database for alias 'default'...
.........................Found 25 test(s).
System check identified no issues (0 silenced).

----------------------------------------------------------------------
Ran 25 tests in 33.596s

OK
Destroying test database for alias 'default'...
```
