# Registro de Interacción IA (CHIA) - Historia de Usuario PSE-14

**Fecha:** 20 de Septiembre de 2026  
**Historia de Usuario:** PSE-14 - Operación de Venta de Divisas (Épica PSE-12: Procesamiento de Operaciones Cambiarias)  
**Asistente:** OpenCode (gemini-3.5-flash-lite)  
**Proyecto:** Global Exchange (Ingeniería de Software 2 - FPUNA)  

---

## 1. Resumen de la Tarea
Implementación completa de la Historia de Usuario **PSE-14**, que permite a los clientes realizar la operación de venta de divisas de forma digital seleccionando divisa origen, cuenta bancaria o billetera digital vinculada obligatoria, monto y aplicando de forma transparente la tasa de compra vigente (buy_rate), límites normativos equivalentes en PYG (50.000 PYG a 1.000.000.000 PYG), comisiones, impuestos y acreditación en la cuenta vinculada.

---

## 2. Criterios de Aceptación Implementados y Probados
1. **Validación de Límites:** Verificación estricta de que el monto de la transacción expresado en guaraníes no exceda el límite máximo de **1.000.000.000 PYG** ni sea inferior al mínimo de **50.000 PYG**.
2. **Aplicación de Tasa de Compra:** Conversión monetaria aplicando estrictamente la tasa de compra vigente (`buy_rate`) provista por la casa de cambio.
3. **Cuenta o Billetera Vinculada Obligatoria:** Verificación estricta de que el cliente cuente obligatoriamente con una cuenta bancaria o billetera digital vinculada y activa para proceder con la acreditación y operación.
4. **Tiempo de Respuesta:** Optimización para garantizar un tiempo de respuesta inferior a **5 segundos** (< 5000 ms).
5. **Pruebas Unitarias e Integración Independientes:** Creación de la suite exclusiva `procesamiento_operaciones/test_PSE_14.py` sin alterar la suite preexistente.
6. **Diseño "Corporate Modern":** Interfaz web estilizada con Tailwind CSS cumpliendo la paleta de colores corporativa y desgloses transparentes.

---

## 3. Comandos Ejecutados
```bash
git checkout -b feature/PSE-14
python manage.py makemigrations
python manage.py migrate
python manage.py test procesamiento_operaciones.test_PSE_14
python manage.py test procesamiento_operaciones.test_PSE_14 procesamiento_operaciones.test_PSE_13 tasas_cambio.test_PSE_10 tasas_cambio.test_PSE_11 tasas_cambio.test_PSE_29 tasas_cambio.test_PSE_30 monitoreo_corporativo.test_PSE_25
```

---

## 4. Evidencia de Pruebas Exitosas
```text
Creating test database for alias 'default'...
.................................Found 33 test(s).
System check identified no issues (0 silenced).

----------------------------------------------------------------------
Ran 33 tests in 59.212s

OK
Destroying test database for alias 'default'...
```

Todas las pruebas unitarias e integración de la historia PSE-14 y de las demás historias del proyecto finalizaron exitosamente (`OK`).
