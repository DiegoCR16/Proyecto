# Registro de Interacción IA (CHIA) - Historia de Usuario PSE-11

## Información de la Tarea
- **Historia de Usuario:** PSE-11 - Simulador de Conversión de Divisas.
- **Asignatura:** Ingeniería de Software 2 - FPUNA.
- **Rama Git:** `feature/PSE-11`
- **Fecha:** 10 de Septiembre de 2026

---

## Criterios de Aceptación Validados
1. **Obligatoriedad de Campos:** Se validó que el simulador requiera obligatoriamente moneda origen, moneda destino y monto, lanzando excepciones `ValidationError` ante campos vacíos o montos inválidos/cero.
2. **Cálculo con Tasas y Beneficios:** Se verificó la precisión matemática en operaciones de compra y venta de divisas, aplicando automáticamente los descuentos según la categoría del cliente (Minorista 0%, VIP 2%, Corporativo 4%).
3. **Manejo de Errores por Tasas Inexistentes:** Se confirmó que al seleccionar una divisa sin tasa registrada, el sistema aborta la simulación y muestra un mensaje de error claro en la interfaz web y en las pruebas unitarias.

---

## Comandos Ejecutados y Pruebas
1. **Creación de Rama:**
   ```bash
   git checkout -b feature/PSE-11
   ```
2. **Ejecución de Pruebas Unitarias (PUD):**
   ```bash
   python manage.py test tasas_cambio
   ```
   *Evidencia:* Todas las pruebas unitarias de PSE-11 (`SimuladorConversionPSE11Tests`) y PSE-9 pasaron exitosamente sin afectar las pruebas preexistentes del proyecto.

---

## Diseño y Componentes (Corporate Modern)
- Implementación de la vista web `currency_simulator_view` y plantilla `tasas_cambio/simulator.html` estilizada con Tailwind CSS.
- Incorporación de panel de previsualización, selectores de moneda, validación numérica y alertas de error visuales.
