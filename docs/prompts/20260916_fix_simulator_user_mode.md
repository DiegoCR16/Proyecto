# Registro de Conversación IA - Desactivación de Beneficios en Simulador bajo "Modo Usuario"

**Fecha:** 16 de Septiembre de 2026  
**Problema:** Cuando un usuario cambiaba al "modo usuario" (no cliente) desde el dashboard de cliente, el simulador de tasas aún aplicaba los beneficios de su categoría de cliente (VIP/Corporativo) en lugar de operar con la simulación estándar.  
**Solución:** Se actualizó `get_user_effective_category(user, request)` para que evalúe si `request.session.get('user_mode', False)` es Verdadero. En caso de estar en "modo usuario", el simulador ignora los beneficios del perfil o cliente activo y aplica la categoría por defecto (`'MINORISTA'`, 0% de beneficio, simulación estándar).  
**Verificación:** Se añadió un test unitario en `tasas_cambio/test_PSE_11.py` y se ejecutó la suite completa (`python manage.py test`), pasando exitosamente los 54 tests.
