# Registro de Conversación IA - Corrección de Zona Horaria en Auditoría y Sistema

**Fecha:** 16 de Septiembre de 2026  
**Problema:** Los registros de auditoría y del sistema mostraban una hora incorrecta (UTC) que no coincidía con la hora local de la computadora / país (Paraguay).  
**Solución:** Se actualizó el parámetro `TIME_ZONE` en `globalexchange/settings.py` de `'UTC'` a `'America/Asuncion'`, alineando todas las marcas de tiempo (`timestamp`) de la auditoría y del sistema con la zona horaria local de Paraguay.  
**Verificación:** Se ejecutó la suite de pruebas unitarias (`python manage.py test`), pasando exitosamente los 53 tests.
