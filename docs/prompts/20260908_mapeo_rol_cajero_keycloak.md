# Registro de Conversación IA (CHIA) - Mapeo de Rol Cajero de Keycloak

**Fecha:** 08/09/2026  
**Historia/Incidente:** Corrección en el mapeo de roles al iniciar sesión vía Keycloak SSO.

## Solicitud del Usuario
"cree un usuario en keycloak y le asigne el rol de cajero y al iniciar sesion no me llevo a la interfaz de cajero por que?"

## Diagnóstico y Análisis
Al analizar el archivo `authentication/views.py`, en el callback del SSO de Keycloak (`keycloak_callback_view`), se identificó que el mapeo de los roles extraídos del token JWT de Keycloak hacia el modelo `Role` local en Django solo contemplaba lo siguiente:
1. Roles `admin`, `administrador` o `administrator` se mapeaban a "Admin".
2. Roles `corporate`, `corporativo`, `empresa` o `jefe` se mapeaban a "Corporate".
3. Cualquier otro rol no contemplado (incluyendo `cajero` o `analista`) caía por defecto en la cláusula `else` asignando el rol "Cliente" (minorista).

Esto causaba que, a pesar de que el usuario tuviera el rol `cajero` asignado en Keycloak, Django le asignara localmente el rol `Cliente`, redirigiéndolo al panel del cliente minorista en lugar de la interfaz operativa del cajero.

## Solución Implementada
1. Se modificó la estructura de control de asignación de roles en `keycloak_callback_view` para incluir explícitamente el mapeo del rol `cajero` al rol local `Cajero`, y del rol `analista` al rol local `Analista`.
2. Se agregó una prueba unitaria `test_sso_keycloak_callback_cajero` en `authentication/tests_PSE_4.py` para simular la autenticación de un usuario con rol de `cajero` en Keycloak y certificar que es mapeado correctamente al rol local en Django y que todas las pruebas pasen satisfactoriamente.
