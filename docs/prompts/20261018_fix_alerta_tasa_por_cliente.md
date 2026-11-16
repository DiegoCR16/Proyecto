# Corrección de Notificaciones de Alerta de Tasa Objetivo por Cliente (PSE-35)

## Contexto del Problema
Anteriormente, cuando una alerta de tasa objetivo (`CurrencyAlert`) alcanzaba el valor esperado en el mercado, el sistema creaba un registro de notificación (`NotificationLog`) asociándolo tanto al cliente como al usuario creador (`user=alert.user`). Esto provocaba que las notificaciones de tasa alcanzada se emitieran y filtraran a nivel de usuario individual en lugar de a nivel de cuenta cliente.

## Solución Implementada
1. **Asociación por Cliente:** En `check_and_trigger_rate_alerts` (`tasas_cambio/views.py`), se ajustó la creación de la notificación `NotificationLog` para que asocie únicamente al cliente (`cliente=alert.cliente`, `user=None`), permitiendo que cualquier usuario operativo o administrador asociado a dicho cliente visualice la notificación de alerta alcanzada.
2. **Pruebas Unitarias:** Se actualizó la prueba unitaria `test_automatic_alert_triggering_on_rate_reach` en `tasas_cambio/test_PSE_35.py` para validar que el registro de notificación resultante tenga configurado el cliente y `user=None`.
