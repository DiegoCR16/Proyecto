# Registro de Conversación IA - Corrección de Detección de Categoría en Simulador de Tasas

**Fecha:** 16 de Septiembre de 2026  
**Problema:** El simulador de tasas en el dashboard no detectaba correctamente el tipo de cliente activo cuando un usuario operaba o cambiaba entre múltiples clientes de distintas categorías (Minorista, VIP, Corporativo), manteniendo siempre la categoría estándar.  
**Solución:** Se implementó la función `get_user_effective_category(user, request)` que verifica el cliente activo actual en la sesión (`active_client_id`) o las relaciones en `UsuarioClienteRelacion` antes de recurrir al perfil predeterminado. Tanto `SimuladorConversionService.simular` como `currency_simulator_view` ahora emplean esta categoría efectiva, reflejando de inmediato los beneficios y umbrales correspondientes al cliente con el que se está operando.  
**Verificación:** Se ejecutó la suite de pruebas unitarias (`python manage.py test`), pasando exitosamente todos los tests.
