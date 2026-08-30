# Registro de Conversación y Desarrollo (CHIA) - Registro Física/Jurídica e integración Keycloak Admin REST API

- **Fecha:** 30 de Agosto de 2026
- **Historia / Requerimiento:** Activación y actualización de `register.html` con selector de Persona Física / Jurídica e integración con Keycloak Admin REST API (`userType`).
- **Asignatura:** Ingeniería de Software 2 - FPUNA

## Resumen de Implementación
1. **Interfaz de Registro (`register.html`):**
   - Incorporación de un selector interactivo (botones tipo radio) antes del formulario para elegir entre **Persona Física** y **Persona Jurídica**.
   - Si se selecciona **Persona Física**, se muestra el formulario completo (nombre completo, cédula/RUC, correo electrónico, contraseña).
   - Si se selecciona **Persona Jurídica**, se muestra un mensaje informativo indicando que el registro de Persona Jurídica no requiere lógica de backend en esta versión.
2. **Lógica de Backend (`views.py`):**
   - Actualización de `register_view` para procesar el tipo de persona (`person_type`).
   - Validaciones de campos, formato y control de duplicados para Persona Física.
   - Conexión con la **Admin REST API de Keycloak** en segundo plano (solicitud de token de administrador vía client_credentials y creación del usuario mediante `/admin/realms/{realm}/users` enviando los atributos `userType: ["fisica"]` y `ci_ruc`).
   - Creación exitosa del usuario local, asignación de rol "Individual" y registro de auditoría (`REGISTER_SUCCESS`).
3. **Pruebas Unitarias (PUN):**
   - Actualización y adición de pruebas unitarias en `authentication/test_PSE_2.py` utilizando `unittest.mock` para verificar la llamada a la Admin REST API de Keycloak y la correcta selección de Persona Física / Jurídica.
   - Ejecución exitosa de la suite completa de pruebas (17 pruebas pasadas).

## Comandos Utilizados
- Ejecución de pruebas unitarias:
  ```bash
  python manage.py test
  ```
