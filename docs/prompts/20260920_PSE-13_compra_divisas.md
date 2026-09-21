# Registro de Interacción IA (CHIA) - Historia de Usuario PSE-13

**Fecha:** 20 de Septiembre de 2026  
**Historia de Usuario:** PSE-13 - Operación de Compra de Divisas (Épica PSE-12: Procesamiento de Operaciones)  
**Asistente:** OpenCode (gemini-3.5-flash-lite)  
**Proyecto:** Global Exchange (Ingeniería de Software 2 - FPUNA)  

---

## 1. Resumen de la Tarea
Implementación completa de la Historia de Usuario **PSE-13**, que permite a los usuarios clientes realizar la compra de divisas de forma digital seleccionando cuenta/billetera de origen, cuenta destino, divisa, monto y método de pago, aplicando de forma transparente tasas de cambio, comisiones, impuestos y el descuento correspondiente según el perfil del cliente (VIP 2% o Corporativo 4%).

---

## 2. Criterios de Aceptación Implementados y Probados
1. **Validación de Límites:** Verificación estricta de que el monto de la transacción expresado en guaraníes no exceda el límite máximo de **1.000.000.000 PYG** ni sea inferior al mínimo de **50.000 PYG**.
2. **Validación de Fondos:** Comprobación estricta de que el método de pago seleccionado posea saldo suficiente para cubrir la transacción (incluyendo comisiones e impuestos) antes de procesarla.
3. **Cálculo Transparente de Tasa y Descuentos:** Aplicación de la tasa de cambio vigente, desglose de comisiones (0.5%), impuestos (IVA 10%) y descuento por perfil de cliente (VIP 2%, Corporativo 4%).
4. **Tiempo de Respuesta:** Optimización para garantizar un tiempo de respuesta inferior a **5 segundos** (< 5000 ms).
5. **Pruebas Unitarias e Integración Independientes:** Creación de la suite exclusiva `tasas_cambio/test_PSE_13.py` sin alterar la suite preexistente.
6. **Diseño "Corporate Modern":** Interfaz web estilizada con Tailwind CSS cumpliendo la paleta de colores corporativa.

---

## 3. Comandos Ejecutados
```bash
git checkout -b feature/PSE-13
python manage.py makemigrations
python manage.py migrate
python manage.py test tasas_cambio.test_PSE_13
python manage.py test tasas_cambio.test_PSE_10 tasas_cambio.test_PSE_11 tasas_cambio.test_PSE_25 tasas_cambio.test_PSE_29 tasas_cambio.test_PSE_30
```

---

## 4. Evidencia de Pruebas Exitosas
```text
Creating test database for alias 'default'...
.....Found 5 test(s).
System check identified no issues (0 silenced).

----------------------------------------------------------------------
Ran 5 tests in 9.422s

OK
Destroying test database for alias 'default'...
```

Todas las pruebas unitarias e integración de la historia PSE-13 y de las demás historias del módulo de tasas de cambio finalizaron exitosamente (`OK`).
