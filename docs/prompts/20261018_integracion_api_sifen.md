# Registro de Conversación IA (CHIA) - Integración API SIFEN (Factura Segura)

**Fecha:** 18 de Octubre, 2026  
**Rama Git:** `feature/PSE-39`  
**Objetivo:** Implementar la integración con el servicio de Facturación Electrónica SIFEN (Factura Segura - ESI) para la plataforma de cambio de divisas (Global Exchange).

## Requerimientos del Prompt (`prompt.txt`)
1. **Autenticación y Token:** Conexión al endpoint de login (`/login?include_auth_token`) enviando credenciales en JSON para obtener el `authentication_token`.
2. **Generación de Documento (`generar_de`):** Construcción del payload exigido por Factura Segura apuntando a `/misife00/v1/esi` con cabeceras obligatorias y operación `generar_de`.
3. **Consulta de Estado y Descarga:** Operación `get_estado_sifen` y peticiones GET para descarga de KuDE (PDF) y XML firmado.
4. **Resistencia y Manejo de Errores:** Uso de la librería `requests`, manejo de excepciones de red y código documentado con docstrings en español adaptados a Django.
5. **Pruebas Unitarias:** Creación de pruebas unitarias con `unittest.mock` para verificar servicios y vistas REST.

## Archivos Creados / Modificados
- `authentication/facturacion_service.py`: Servicio Django con la lógica de integración con Factura Segura.
- `authentication/views.py`: Vistas REST de API para autenticación, generación de DE, consulta de estado y descarga de KuDE/XML.
- `authentication/urls.py`: Endpoints API registrados bajo `/api/sifen/`.
- `authentication/tests_sifen.py`: Pruebas unitarias completas con mocks para la API de SIFEN.
- `docs/prompts/20261018_integracion_api_sifen.md`: Este registro de conversación (CHIA).

## Verificación
- Ejecución exitosa de la suite de pruebas unitarias de Django (`python manage.py test`), superando todas las pruebas sin errores.
