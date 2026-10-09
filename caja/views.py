# -*- coding: utf-8 -*-
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.core.exceptions import ValidationError
from decimal import Decimal

from .models import (
    Caja, TurnoCaja, DenominacionDivisa, DesgloseEfectivoCaja, 
    DetalleDesgloseBillete, ArqueoCaja, BitacoraArqueo, 
    verificar_turno_activo, inicializar_denominaciones_default, procesar_arqueo_cierre
)
from authentication.models import UserProfile
from gestion_clientes.views import get_user_interface_context


@login_required
def gestion_caja_view(request):
    """
    Vista web para la gestión de apertura y cierre de turnos de caja física
    y registro de ingresos/salidas con desglose detallado de billetes (PSE-21),
    incluyendo el reporte de arqueo automático y bitácora de auditoría (PSE-22).
    Estilizada con Tailwind CSS (Corporate Modern).
    """
    inicializar_denominaciones_default()

    if not Caja.objects.exists():
        Caja.objects.get_or_create(codigo="C01", defaults={'nombre': "Caja Principal 01", 'activa': True})
        Caja.objects.get_or_create(codigo="C02", defaults={'nombre': "Caja Secundaria 02", 'activa': True})

    cajas = Caja.objects.filter(activa=True)
    cajas_data = []

    denominaciones = DenominacionDivisa.objects.filter(activa=True)
    denominaciones_por_divisa = {}
    for d in denominaciones:
        if d.divisa not in denominaciones_por_divisa:
            denominaciones_por_divisa[d.divisa] = []
        denominaciones_por_divisa[d.divisa].append(d)

    for caja in cajas:
        turno_activo = verificar_turno_activo(caja)
        desgloses_turno = DesgloseEfectivoCaja.objects.filter(turno=turno_activo).order_by('-fecha') if turno_activo else []
        cajas_data.append({
            'caja': caja,
            'turno_activo': turno_activo,
            'desgloses_turno': desgloses_turno,
        })

    turnos_cerrados = TurnoCaja.objects.filter(estado='CERRADO').prefetch_related('arqueos', 'bitacoras_arqueo').order_by('-fecha_cierre')[:10]
    bitacoras_recientes = BitacoraArqueo.objects.all().order_by('-fecha')[:15]

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    interface_ctx = get_user_interface_context(request, profile)
    is_admin = interface_ctx.get('is_admin', False)
    badge_text = interface_ctx.get('badge_text', '')

    if is_admin or (badge_text and 'admin' in badge_text.lower()):
        role_badge = "Administrador"
        badge_bg = "bg-blue-800"
    else:
        role_badge = badge_text if badge_text else "Cajero de Sucursal"
        badge_bg = "bg-amber-700"

    context = {
        'cajas_data': cajas_data,
        'denominaciones_por_divisa': denominaciones_por_divisa,
        'turnos_cerrados': turnos_cerrados,
        'bitacoras_recientes': bitacoras_recientes,
        'role_badge': role_badge,
        'badge_bg': badge_bg,
    }
    return render(request, 'caja/gestion_caja.html', context)


@login_required
def abrir_turno_view(request, caja_id):
    """
    Procesa la apertura de un turno de caja asociando el usuario cajero,
    la fecha/hora y los saldos iniciales por tipo de divisa (USD, EUR, PYG, BRL, ARS).
    """
    caja = get_object_or_404(Caja, id=caja_id, activa=True)

    if verificar_turno_activo(caja):
        messages.error(request, f"La caja '{caja.nombre}' ya posee un turno activo sin cerrar.")
        return redirect('caja:gestion_caja')

    if request.method == 'POST':
        try:
            saldo_pyg = Decimal(request.POST.get('saldo_inicial_pyg') or '0')
            saldo_usd = Decimal(request.POST.get('saldo_inicial_usd') or '0')
            saldo_eur = Decimal(request.POST.get('saldo_inicial_eur') or '0')
            saldo_brl = Decimal(request.POST.get('saldo_inicial_brl') or '0')
            saldo_ars = Decimal(request.POST.get('saldo_inicial_ars') or '0')

            turno = TurnoCaja(
                caja=caja,
                cajero=request.user,
                saldo_inicial_pyg=saldo_pyg,
                saldo_inicial_usd=saldo_usd,
                saldo_inicial_eur=saldo_eur,
                saldo_inicial_brl=saldo_brl,
                saldo_inicial_ars=saldo_ars,
                estado='ABIERTO'
            )
            turno.save()
            messages.success(request, f"Turno abierto exitosamente para la caja '{caja.nombre}' con usuario {request.user.username}.")
        except ValidationError as e:
            messages.error(request, str(e))
        except Exception as e:
            messages.error(request, f"Error al abrir turno: {str(e)}")

    return redirect('caja:gestion_caja')


@login_required
def cerrar_turno_view(request, turno_id):
    """
    Procesa el cierre de turno de caja ingresando los saldos finales físicos,
    ejecutando el arqueo automático por divisa, registrando bitácora de auditoría
    y notificando al Administrador en caso de descuadres (PSE-22).
    """
    turno = get_object_or_404(TurnoCaja, id=turno_id, estado='ABIERTO')

    if request.method == 'POST':
        try:
            turno.saldo_final_pyg = Decimal(request.POST.get('saldo_final_pyg') or '0')
            turno.saldo_final_usd = Decimal(request.POST.get('saldo_final_usd') or '0')
            turno.saldo_final_eur = Decimal(request.POST.get('saldo_final_eur') or '0')
            turno.saldo_final_brl = Decimal(request.POST.get('saldo_final_brl') or '0')
            turno.saldo_final_ars = Decimal(request.POST.get('saldo_final_ars') or '0')
            turno.fecha_cierre = timezone.now()
            turno.estado = 'CERRADO'
            turno.save()

            procesar_arqueo_cierre(turno)

            messages.success(request, f"Turno #{turno.id} cerrado y arqueo automático procesado exitosamente. Operaciones congeladas.")
        except Exception as e:
            messages.error(request, f"Error al cerrar turno y procesar arqueo: {str(e)}")

    return redirect('caja:gestion_caja')


@login_required
def registrar_movimiento_efectivo_view(request, turno_id):
    """
    Procesa un ingreso o salida física de efectivo en un turno activo,
    respaldado por el desglose detallado de billetes por denominación (PSE-21).
    """
    turno = get_object_or_404(TurnoCaja, id=turno_id, estado='ABIERTO')

    if request.method == 'POST':
        try:
            tipo_operacion = request.POST.get('tipo_operacion') # INGRESO o SALIDA
            divisa = request.POST.get('divisa', 'PYG')
            observacion = request.POST.get('observacion', '')

            if tipo_operacion not in ['INGRESO', 'SALIDA']:
                messages.error(request, "Tipo de operación no válido.")
                return redirect('caja:gestion_caja')

            desglose = DesgloseEfectivoCaja.objects.create(
                turno=turno,
                tipo_operacion=tipo_operacion,
                divisa=divisa,
                observacion=observacion,
                monto_total=Decimal('0.00')
            )

            monto_total = Decimal('0.00')
            denominaciones = DenominacionDivisa.objects.filter(divisa=divisa, activa=True)
            
            for denom in denominaciones:
                qty_str = request.POST.get(f'denom_{denom.id}', '0')
                qty = int(qty_str) if qty_str.isdigit() else 0
                if qty > 0:
                    detalle = DetalleDesgloseBillete.objects.create(
                        desglose=desglose,
                        denominacion=denom,
                        cantidad=qty
                    )
                    monto_total += detalle.subtotal

            desglose.monto_total = monto_total
            desglose.save()

            messages.success(request, f"{tipo_operacion.capitalize()} de efectivo registrado exitosamente ({divisa} {monto_total:,.2f}) con desglose detallado de billetes.")
        except Exception as e:
            messages.error(request, f"Error al registrar movimiento con desglose: {str(e)}")

    return redirect('caja:gestion_caja')
