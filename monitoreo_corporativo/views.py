# -*- coding: utf-8 -*-
"""
Vistas del módulo de Monitoreo Corporativo.

PSE-41: panel de reportes financieros de transacciones y ganancias, con vista previa web,
descarga en CSV (se abre en Excel) y vista de impresión para guardar como PDF desde el navegador.

PSE-24: dashboard de ganancias cambiarias en tiempo real (polling a una API JSON) con gráfico
de barras mensual por divisa, consolidado en PYG.
"""
from django import forms
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone

from authentication.models import Cliente
from authentication.permisos import requiere_permiso
from tasas_cambio.models import ExchangeRate

from . import dashboard, reportes

FORMATO_CSV = 'csv'
FORMATO_PDF = 'pdf'


def opciones_monedas():
    """
    Divisas disponibles para filtrar (las registradas en el catálogo más PYG).

    Returns:
        list[tuple[str, str]]: Pares (código, etiqueta).
    """
    monedas = [('PYG', 'PYG - Guaraní')]
    for codigo, nombre in ExchangeRate.objects.order_by('currency_code').values_list('currency_code', 'currency_name'):
        if codigo != 'PYG':
            monedas.append((codigo, f'{codigo} - {nombre}'))
    return monedas


class FiltroReporteForm(forms.Form):
    """
    Formulario (GET) de filtros combinables del reporte financiero.

    Todos los campos son opcionales; un campo vacío no filtra.
    """
    fecha_desde = forms.DateField(required=False, label='Desde', widget=forms.DateInput(attrs={'type': 'date'}))
    fecha_hasta = forms.DateField(required=False, label='Hasta', widget=forms.DateInput(attrs={'type': 'date'}))
    tipo = forms.ChoiceField(required=False, label='Tipo de operación', choices=[('', 'Todas')] + reportes.TIPOS_OPERACION)
    moneda = forms.ChoiceField(required=False, label='Moneda')
    cajero = forms.ModelChoiceField(required=False, label='Cajero', queryset=User.objects.none(), empty_label='Todos')
    cliente = forms.ModelChoiceField(required=False, label='Cliente', queryset=Cliente.objects.none(), empty_label='Todos')

    def __init__(self, *args, **kwargs):
        """Carga las opciones dinámicas de moneda, cajero (usuarios con turnos) y cliente."""
        super().__init__(*args, **kwargs)
        self.fields['moneda'].choices = [('', 'Todas')] + opciones_monedas()
        self.fields['cajero'].queryset = User.objects.filter(turnocaja__isnull=False).distinct().order_by('username')
        self.fields['cajero'].label_from_instance = lambda u: u.get_full_name() or u.username
        self.fields['cliente'].queryset = Cliente.objects.order_by('nombre_o_razon_social')
        self.fields['cliente'].label_from_instance = lambda c: f'{c.nombre_o_razon_social} ({c.documento_identidad})'
        for campo in self.fields.values():
            campo.widget.attrs['class'] = 'campo'

    def clean(self):
        """Valida que la fecha inicial no sea posterior a la final."""
        datos = super().clean()
        desde, hasta = datos.get('fecha_desde'), datos.get('fecha_hasta')
        if desde and hasta and desde > hasta:
            raise forms.ValidationError('La fecha "Desde" no puede ser posterior a la fecha "Hasta".')
        return datos

    def a_filtros(self):
        """
        Convierte los datos validados en un ``FiltrosReporte``.

        Returns:
            reportes.FiltrosReporte: Filtros listos para el servicio de reportes.
        """
        datos = self.cleaned_data
        return reportes.FiltrosReporte(
            fecha_desde=datos.get('fecha_desde'),
            fecha_hasta=datos.get('fecha_hasta'),
            tipo=datos.get('tipo') or '',
            moneda=datos.get('moneda') or '',
            cajero_id=datos['cajero'].pk if datos.get('cajero') else None,
            cliente_id=datos['cliente'].pk if datos.get('cliente') else None,
        )


@login_required
@requiere_permiso('can_view_financial_reports')
def reporte_financiero_view(request):
    """
    Panel de reportes financieros (PSE-41).

    Muestra los filtros y la vista previa del informe con sus totales. Con los mismos filtros:

    * ``?formato=csv`` descarga el CSV (Excel/CSV).
    * ``?formato=pdf`` muestra la vista de impresión, que se guarda como PDF con
      ``window.print()`` (mismo enfoque que la planilla de cierre de caja).

    Args:
        request (HttpRequest): Solicitud GET con los filtros.

    Returns:
        HttpResponse: Página del reporte, vista de impresión o archivo CSV.
    """
    form = FiltroReporteForm(request.GET or None)
    filas, totales, filtros_validos = [], reportes.TotalesReporte(), True
    filtros = reportes.FiltrosReporte()
    if form.is_bound:
        filtros_validos = form.is_valid()
        if filtros_validos:
            filtros = form.a_filtros()
    if filtros_validos:
        filas, totales = reportes.generar_reporte(filtros)

    formato = request.GET.get('formato', '').lower()
    if formato in (FORMATO_CSV, FORMATO_PDF) and filtros_validos:
        datos = form.cleaned_data if form.is_bound else {}
        descripcion = reportes.descripcion_filtros(filtros, datos.get('cajero'), datos.get('cliente'))
        if formato == FORMATO_CSV:
            respuesta = HttpResponse(reportes.exportar_csv(filas, totales, descripcion), content_type='text/csv; charset=utf-8')
            nombre = f"reporte_financiero_{timezone.localtime().strftime('%Y%m%d_%H%M')}.csv"
            respuesta['Content-Disposition'] = f'attachment; filename="{nombre}"'
            return respuesta
        return render(request, 'monitoreo_corporativo/reporte_financiero_imprimir.html', {
            'filas': filas,
            'totales': totales,
            'descripcion': descripcion,
            'generado': timezone.localtime(),
        })

    consulta = request.GET.copy()
    consulta.pop('formato', None)
    return render(request, 'monitoreo_corporativo/reporte_financiero.html', {
        'form': form,
        'filas': filas,
        'totales': totales,
        'consulta': consulta.urlencode(),
    })


def _meses_solicitados(request):
    """
    Lee el parámetro ``meses`` del gráfico (6 o 12).

    Args:
        request (HttpRequest): Solicitud GET.

    Returns:
        int: Meses válidos; si el valor no es válido, el valor por defecto.
    """
    try:
        meses = int(request.GET.get('meses', dashboard.MESES_POR_DEFECTO))
    except (TypeError, ValueError):
        return dashboard.MESES_POR_DEFECTO
    return meses if meses in dashboard.MESES_PERMITIDOS else dashboard.MESES_POR_DEFECTO


@login_required
@requiere_permiso('can_view_financial_reports')
def dashboard_ganancias_view(request):
    """
    Dashboard de monitoreo de ganancias cambiarias (PSE-24).

    Renderiza los KPIs, las cotizaciones activas y el gráfico con los datos iniciales;
    luego la página se actualiza sola consultando ``dashboard_ganancias_api``.

    Args:
        request (HttpRequest): Solicitud GET (``meses`` opcional: 6 o 12).

    Returns:
        HttpResponse: Página del dashboard.
    """
    meses = _meses_solicitados(request)
    return render(request, 'monitoreo_corporativo/dashboard_ganancias.html', {
        'datos': dashboard.datos_dashboard(meses),
        'datos_json': dashboard.datos_dashboard_json(meses),
        'meses': meses,
        'meses_permitidos': dashboard.MESES_PERMITIDOS,
        'intervalo_segundos': dashboard.INTERVALO_ACTUALIZACION_SEGUNDOS,
    })


@login_required
@requiere_permiso('can_view_financial_reports')
def dashboard_ganancias_api(request):
    """
    API JSON del dashboard de ganancias (PSE-24), consultada periódicamente por la página.

    Args:
        request (HttpRequest): Solicitud GET (``meses`` opcional: 6 o 12).

    Returns:
        JsonResponse: KPIs del día y del mes, cotizaciones activas y datos del gráfico, en PYG.
    """
    return JsonResponse(dashboard.datos_dashboard_json(_meses_solicitados(request)))
