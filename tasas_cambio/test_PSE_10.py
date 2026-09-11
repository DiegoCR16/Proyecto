# -*- coding: utf-8 -*-
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from tasas_cambio.models import ExchangeRate, ExchangeRateHistory


class RatesEvolutionPSE10Tests(TestCase):
    """
    Suite de pruebas unitarias independiente y exclusiva para la Historia de Usuario PSE-10:
    Gráfico de Evolución de Tasas de Cambio.
    Valida la correcta consulta y formateo de datos históricos por rango temporal,
    la estructura de respuesta JSON para el renderizado del gráfico, y la gestión
    adecuada ante la ausencia de datos históricos sin romper la interfaz.
    """

    def setUp(self):
        """
        Configura el cliente de prueba y datos históricos iniciales para divisas (USD, XXX).
        """
        self.client = Client()
        self.api_url = reverse('tasas_cambio:rates_evolution_api')
        self.web_url = reverse('tasas_cambio:rates_evolution')

        # Crear registros históricos para USD
        now = timezone.now()
        ExchangeRateHistory.objects.all().delete() # Limpiar mock por defecto para pruebas precisas

        # Insertar 10 días de registros históricos de prueba para USD
        for i in range(10, 0, -1):
            ExchangeRateHistory.objects.create(
                currency_code='USD',
                buy_rate=Decimal(str(7200 + i * 10)),
                sell_rate=Decimal(str(7350 + i * 10)),
                timestamp=now - timedelta(days=i)
            )

    def test_historical_data_query_and_formatting(self):
        """
        Valida la correcta consulta, filtrado por rango de tiempo (ej. 7D, 30D)
        y el formato adecuado de las tasas históricas de compra y venta.
        """
        response = self.client.get(f"{self.api_url}?currency=USD&range=7D")
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertTrue(data['has_data'])
        self.assertEqual(data['currency'], 'USD')
        self.assertEqual(data['range'], '7D')
        self.assertGreater(len(data['labels']), 0)
        self.assertEqual(len(data['labels']), len(data['buy_rates']))
        self.assertEqual(len(data['labels']), len(data['sell_rates']))

        # Verificar que los valores sean numéricos decimales válidos
        for buy in data['buy_rates']:
            self.assertIsInstance(buy, float)
            self.assertGreater(buy, 0.0)

        for sell in data['sell_rates']:
            self.assertIsInstance(sell, float)
            self.assertGreater(sell, 0.0)

    def test_chart_json_response_structure(self):
        """
        Valida que la estructura de la respuesta JSON contenga todas las claves requeridas
        para la correcta renderización interactiva con Chart.js (has_data, currency, range,
        message, labels, buy_rates, sell_rates).
        """
        response = self.client.get(f"{self.api_url}?currency=USD&range=30D")
        self.assertEqual(response.status_code, 200)

        data = response.json()
        expected_keys = ['has_data', 'currency', 'range', 'message', 'labels', 'buy_rates', 'sell_rates']
        for key in expected_keys:
            self.assertIn(key, data)

    def test_absence_of_historical_data_handling(self):
        """
        Valida la gestión adecuada ante la ausencia de datos históricos para una divisa
        inexistente o sin registros en el rango, retornando has_data=False y un mensaje
        descriptivo sin errores de servidor.
        """
        response = self.client.get(f"{self.api_url}?currency=XXX&range=7D")
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertFalse(data['has_data'])
        self.assertEqual(data['currency'], 'XXX')
        self.assertIn("No se registran datos históricos suficientes", data['message'])
        self.assertEqual(data['labels'], [])
        self.assertEqual(data['buy_rates'], [])
        self.assertEqual(data['sell_rates'], [])

    def test_evolution_web_view_integration(self):
        """
        Valida que la vista web HTTP del gráfico de evolución responda correctamente (status 200)
        y contenga los elementos visuales clave de la interfaz.
        """
        response = self.client.get(self.web_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gráfico de Evolución de Tasas de Cambio")
        self.assertContains(response, "Panel de Filtros")
        self.assertContains(response, "evolutionChart")
