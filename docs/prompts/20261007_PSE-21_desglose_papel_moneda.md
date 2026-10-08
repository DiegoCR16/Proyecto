# Registro de Conversación IA (CHIA) - Historia de Usuario PSE-21: Registro Detallado y Desglose de Papel Moneda

**Fecha:** 07/10/2026  
**Historia de Usuario:** PSE-21: Registro Detallado y Desglose de Papel Moneda (Epic: Gestión de Caja).  
**Rama Git:** `feature/PSE-21`

## Resumen de Requerimientos Implementados

1. **Modelos de Datos y Estructura (PSE-21):**
   - **`DenominacionDivisa`**: Almacena las denominaciones de papel moneda autorizadas por divisa (`PYG`, `USD`, `EUR`, `BRL`, `ARS`) con sus valores faciales y nombres descriptivos.
   - **`DesgloseEfectivoCaja`**: Registra conteos físicos o movimientos de efectivo (`APERTURA`, `CIERRE`, `INGRESO`, `SALIDA`) respaldados por el desglose detallado de billetes.
   - **`DetalleDesgloseBillete`**: Vincula cada desglose con la cantidad de billetes por denominación, calculando automáticamente el subtotal mediante la propiedad `cantidad * valor_facial`.

2. **Lógica de Negocio y Vistas:**
   - Inicialización automática de denominaciones estándar para todas las divisas autorizadas.
   - Vista web en la aplicación `caja` (`gestion_caja_view` y `registrar_movimiento_efectivo_view`) para procesar ingresos y salidas físicas de efectivo con desglose detallado de billetes.

3. **Interfaz de Usuario ("Corporate Modern"):**
   - Matriz / Formulario tabular organizado por divisa con inputs numéricos para la cantidad de billetes y etiquetas de su valor facial.
   - Calculadora reactiva en tiempo real (JavaScript) que calcula subtotales por denominación y el total general de forma inmediata al alterar cualquier cantidad.

4. **Pruebas Unitarias Exclusivas (PUN):**
   - Creación del archivo `caja/test_PSE_21.py` de forma aditiva y exclusiva para PSE-21, validando:
     - La correcta estructura de almacenamiento y asociación de denominaciones por divisa autorizada.
     - La vinculación exacta entre el desglose de billetes y los movimientos de entrada/salida de efectivo.
     - La precisión matemática de la sumatoria automática de subtotales en base a las cantidades ingresadas.

5. **Documentación de Código (PDO):**
   - Incorporación de Docstrings en formato Google/Sphinx en todas las clases, modelos y funciones creadas o modificadas.
