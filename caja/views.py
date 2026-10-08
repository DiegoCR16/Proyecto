# -*- coding: utf-8 -*-
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.core.exceptions import ValidationError
from decimal import Decimal
from .models import Caja, TurnoCaja, verificar_turno_activo

@login_required
def gestion_caja_view(request):
    """
    Vista web para la gestión de apertura y cierre de turnos de caja física.
    Muestra el estado activo de cada caja y formularios con saldos por divisa (USD, EUR, PYG, BRL, ARS).
    Estilizada con Tailwind CSS (Corporate Modern).
    """
    cajas = Caja.objects.filter(activa=True)
    cajas_data = []

    for caja in cajas:
        turno_activo = verificar_turno_activo(caja)
        cajas_data.append({
            'caja': caja,
            'turno_activo': turno_activo,
        })

    context = {
        'cajas_data': cajas_data,
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
    Procesa el cierre de turno de caja ingresando los saldos finales físicos
    y registrando la hora de cierre para congelar las operaciones de esa caja.
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
            messages.success(request, f"Turno #{turno.id} cerrado correctamente. Las operaciones en la caja '{turno.caja.nombre}' han sido congeladas.")
        except Exception as e:
            messages.error(request, f"Error al cerrar turno: {str(e)}")

    return redirect('caja:gestion_caja')
