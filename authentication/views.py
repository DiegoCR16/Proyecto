import requests
import base64
import json
import logging
import re
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.models import User
from django.views.decorators.csrf import ensure_csrf_cookie
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.db import models
from decimal import Decimal
from django.utils import timezone
from .models import UserProfile, AuditLog, Role, Permission, CorporateGroup, GroupMembership, ClientRegistrationRequest, MemberRequest
from tasas_cambio.models import ExchangeRate
from gestion_clientes.views import get_user_interface_context
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, BasePermission
from . import mfa
from .facturacion_service import FacturaSeguraService
from .usuarios import obtener_o_crear_usuario_keycloak
from .estado_interfaz import INTERVALO_SEGUNDOS as INTERVALO_AUTO_ACTUALIZACION, estado_interfaz
from .permisos import (
    ROL_ADMIN, ROL_ANALISTA, ROL_CLIENTE, ROL_CAJERO, ROL_JEFE, ROL_OPERADOR, ROL_ANALISTA_CORP,
    ROLES_DE_CLIENTE, PERMISOS_ADMINISTRATIVOS, NOMBRES_PERMISOS, TODOS_LOS_PERMISOS,
    asegurar_catalogo_permisos, es_rol_admin, permisos_del_rol, rol_efectivo, tiene_permiso, requiere_permiso,
)

logger = logging.getLogger(__name__)

def get_client_ip(request):
    """
    Obtiene la dirección IP del cliente desde la request HTTP.
    
    Args:
        request (HttpRequest): Objeto de petición HTTP de Django.
        
    Returns:
        str: Dirección IP del cliente.
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip

@ensure_csrf_cookie
def login_view(request):
    """
    Vista de inicio de sesión principal. Muestra el acceso a Keycloak SSO
    y la pizarra pública de tasas de cambio en tiempo real (PSE-9) para invitados y usuarios.
    
    Args:
        request (HttpRequest): Objeto de petición HTTP de Django.
        
    Returns:
        HttpResponse: Respuesta renderizada con la plantilla de login y cotizaciones.
    """
    default_rates = [
        ('USD', 'Dólar Estadounidense', Decimal('7300.0000'), Decimal('7450.0000')),
        ('EUR', 'Euro', Decimal('7900.0000'), Decimal('8150.0000')),
        ('BRL', 'Real Brasileño', Decimal('1350.0000'), Decimal('1450.0000')),
        ('ARS', 'Peso Argentino', Decimal('7.5000'), Decimal('9.0000')),
        ('PYG', 'Guaraní Paraguayo', Decimal('1.0000'), Decimal('1.0000')),
    ]

    for code, name, buy, sell in default_rates:
        ExchangeRate.objects.get_or_create(
            currency_code=code,
            defaults={
                'currency_name': name,
                'buy_rate': buy,
                'sell_rate': sell
            }
        )

    rates = ExchangeRate.objects.all().order_by('id')

    user_profile = None
    benefit_percentage = Decimal('0.00')
    benefit_label = 'Estándar'
    category_display = 'Invitado (Acceso Público)'

    if request.user.is_authenticated:
        try:
            user_profile = request.user.profile
            category = user_profile.category
            if category == 'VIP':
                benefit_percentage = Decimal('2.00')
                benefit_label = '2% (VIP)'
                category_display = 'VIP'
            elif category == 'CORPORATIVO':
                benefit_percentage = Decimal('4.00')
                benefit_label = '4% (Corporativo)'
                category_display = 'Corporativo'
            else:
                category_display = 'Minorista'
        except Exception:
            pass

    personalized_rates = []
    for rate in rates:
        if benefit_percentage > 0 and rate.currency_code != 'PYG':
            factor = Decimal('1.00') - (benefit_percentage / Decimal('100.00'))
            custom_sell = (rate.sell_rate * factor).quantize(Decimal('0.0001'))
            custom_buy = (rate.buy_rate / factor).quantize(Decimal('0.0001'))
        else:
            custom_sell = rate.sell_rate
            custom_buy = rate.buy_rate

        personalized_rates.append({
            'currency_code': rate.currency_code,
            'currency_name': rate.currency_name,
            'standard_buy': rate.buy_rate,
            'standard_sell': rate.sell_rate,
            'custom_buy': custom_buy,
            'custom_sell': custom_sell,
            'last_updated': rate.last_updated,
        })

    context = {
        'rates': personalized_rates,
        'user_profile': user_profile,
        'benefit_percentage': benefit_percentage,
        'benefit_label': benefit_label,
        'category_display': category_display,
        'now': timezone.now(),
    }

    return render(request, 'authentication/login.html', context)

def keycloak_login_redirect(request):
    """
    Redirige al servidor de Keycloak para iniciar el flujo OIDC / SSO de autenticación.
    
    Args:
        request (HttpRequest): Objeto de petición HTTP de Django.
        
    Returns:
        HttpResponseRedirect: Redirección al endpoint de autenticación de Keycloak.
    """
    keycloak_url = f"{getattr(settings, 'KEYCLOAK_FRONTEND_URL', settings.KEYCLOAK_SERVER_URL)}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/auth"
    params = f"?client_id={settings.KEYCLOAK_CLIENT_ID}&redirect_uri={settings.KEYCLOAK_REDIRECT_URI}&response_type=code&scope=openid"
    return redirect(keycloak_url + params)

def keycloak_register_redirect(request):
    """
    Redirige al servidor de Keycloak para iniciar el flujo de registro de nuevos usuarios (IdP).
    
    Args:
        request (HttpRequest): Objeto de petición HTTP de Django.
        
    Returns:
        HttpResponseRedirect: Redirección al endpoint de registro de Keycloak.
    """
    keycloak_reg_url = f"{getattr(settings, 'KEYCLOAK_FRONTEND_URL', settings.KEYCLOAK_SERVER_URL)}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/registrations"
    params = f"?client_id={settings.KEYCLOAK_CLIENT_ID}&redirect_uri={settings.KEYCLOAK_REDIRECT_URI}&response_type=code&scope=openid&kc_action=register"
    return redirect(keycloak_reg_url + params)

@ensure_csrf_cookie
def keycloak_callback_view(request):
    """
    Callback del SSO de Keycloak tras autenticación exitosa.
    Intercambia el código por tokens y obtiene roles y grupos directamente desde Keycloak (Fuente de Verdad Principal).
    """
    code = request.GET.get('code')
    ip = get_client_ip(request)

    if not code:
        AuditLog.objects.create(
            user=request.user if request.user.is_authenticated else None,
            action="SSO_CALLBACK_FAILED",
            ip_address=ip,
            details="Código de autorización ausente en callback de Keycloak."
        )
        return HttpResponseBadRequest("Código de autorización ausente.")

    token_url = f"{settings.KEYCLOAK_SERVER_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/token"
    token_data = {
        'grant_type': 'authorization_code',
        'client_id': settings.KEYCLOAK_CLIENT_ID,
        'code': code,
        'redirect_uri': settings.KEYCLOAK_REDIRECT_URI,
    }
    if getattr(settings, 'KEYCLOAK_CLIENT_SECRET', None):
        token_data['client_secret'] = settings.KEYCLOAK_CLIENT_SECRET
    
    try:
        token_response = requests.post(token_url, data=token_data)
        if token_response.status_code != 200:
            return HttpResponseBadRequest(f"Error al autenticar con Keycloak: {token_response.text}")
        
        token_json = token_response.json()
        access_token = token_json.get('access_token')

        token_parts = access_token.split('.')
        payload_encoded = token_parts[1]
        payload_encoded += '=' * (-len(payload_encoded) % 4)
        payload_json = json.loads(base64.urlsafe_b64decode(payload_encoded).decode('utf-8'))

        username = payload_json.get('preferred_username', 'keycloak_user')
        email = payload_json.get('email', '')
        kc_id = payload_json.get('sub')
        
        realm_roles = payload_json.get('realm_access', {}).get('roles', [])
        client_roles = payload_json.get('resource_access', {}).get(settings.KEYCLOAK_CLIENT_ID, {}).get('roles', [])
        all_roles = list(set(realm_roles + client_roles))

        # Consultar Keycloak Admin API para obtener los grupos y roles exactos del usuario en Keycloak
        kc_groups = []
        try:
            admin_token_url = f"{settings.KEYCLOAK_SERVER_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/token"
            admin_token_data = {
                'grant_type': 'client_credentials',
                'client_id': settings.KEYCLOAK_CLIENT_ID,
                'client_secret': getattr(settings, 'KEYCLOAK_CLIENT_SECRET', ''),
            }
            admin_token_resp = requests.post(admin_token_url, data=admin_token_data, timeout=3)
            if admin_token_resp.status_code == 200:
                admin_token = admin_token_resp.json().get('access_token')
                headers = {'Authorization': f'Bearer {admin_token}', 'Content-Type': 'application/json'}
                
                if not kc_id:
                    search_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users?username={username}"
                    search_resp = requests.get(search_url, headers=headers, timeout=3)
                    if search_resp.status_code == 200 and search_resp.json():
                        kc_id = search_resp.json()[0].get('id')

                if kc_id:
                    roles_api_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users/{kc_id}/role-mappings/realm"
                    roles_api_resp = requests.get(roles_api_url, headers=headers, timeout=3)
                    if roles_api_resp.status_code == 200:
                        api_roles = [r.get('name') for r in roles_api_resp.json()]
                        all_roles = list(set(all_roles + api_roles))

                    groups_api_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users/{kc_id}/groups"
                    groups_api_resp = requests.get(groups_api_url, headers=headers, timeout=3)
                    if groups_api_resp.status_code == 200:
                        kc_groups = groups_api_resp.json()
        except Exception:
            pass

        all_roles_lower = [r.lower() for r in all_roles]

        # Keycloak guarda el username en minúsculas: se busca sin distinguir mayúsculas
        # para no duplicar usuarios creados desde la administración (ej. "Diego" / "diego").
        user, created = obtener_o_crear_usuario_keycloak(username, kc_id=kc_id, defaults={'email': email})
        if email and not user.email:
            user.email = email
            user.save()

        role_obj = None

        if any(r in all_roles_lower for r in ['admin', 'administrador', 'administrator']):
            role_obj, _ = Role.objects.get_or_create(name="Admin")
        elif any(r in all_roles_lower for r in ['cajero']):
            role_obj, _ = Role.objects.get_or_create(name="Cajero")
        elif any(r in all_roles_lower for r in ['analista']):
            role_obj, _ = Role.objects.get_or_create(name="Analista")
        elif 'cliente' in all_roles_lower:
            role_obj, _ = Role.objects.get_or_create(name="Cliente")
        else:
            # Usuario regular: sin rol hasta que el Administrador le asigne uno
            # o se le apruebe una solicitud de cliente.
            role_obj = None

        profile, _ = UserProfile.objects.get_or_create(user=user)
        if kc_id:
            profile.keycloak_id = kc_id
        profile.role = role_obj
        # "Corporativo" ya no viene de los roles de Keycloak: es una categoría de beneficio del cliente.
        profile.is_corporate = False
        profile.save()

        for g in kc_groups:
            g_id = g.get('id')
            g_name = g.get('name')
            corp_group, _ = CorporateGroup.objects.get_or_create(
                keycloak_group_id=g_id,
                defaults={'group_name': g_name, 'juridica_profile': profile}
            )
            role_in_group = 'OPERADOR'
            if any(r.lower() == f"{g_name.lower()}_cliente" or r.lower() == 'cliente' or r.lower() == f"{g_name.lower()}_jefe" or r.lower() == 'jefe' for r in all_roles_lower):
                role_in_group = 'CLIENTE'
            elif any(r.lower() == f"{g_name.lower()}_analista" or r.lower() == 'analista' for r in all_roles_lower):
                role_in_group = 'ANALISTA'

            GroupMembership.objects.get_or_create(
                corporate_group=corp_group,
                fisica_profile=profile,
                defaults={'role_in_group': role_in_group}
            )

        login(request, user)
        AuditLog.objects.create(
            user=user,
            action="SSO_LOGIN_SUCCESS",
            ip_address=ip,
            details=f"Inicio de sesión SSO exitoso. Roles Keycloak: {all_roles}, Grupos: {[g.get('name') for g in kc_groups]}"
        )

        if profile.requires_mfa():
            # iToken por correo: código nuevo en cada inicio de sesión.
            profile.itoken_verified = False
            profile.save(update_fields=['itoken_verified'])
            mfa.iniciar_verificacion(request, user)
            return redirect('mfa_verify')
        return redirect('dashboard_redirect')

    except Exception as e:
        return HttpResponseBadRequest(f"Error en comunicación con Keycloak: {str(e)}")

@ensure_csrf_cookie
def mfa_verify_view(request):
    """
    Verificación en dos pasos (iToken) con código enviado por correo.

    El código de 6 dígitos se genera y envía al iniciar sesión (ver authentication.mfa);
    vence a los 5 minutos, es de uso único y se bloquea tras 5 intentos fallidos.
    Con action=reenviar se envía un código nuevo (como máximo uno por minuto).

    Args:
        request (HttpRequest): Objeto de petición HTTP de Django.

    Returns:
        HttpResponse: Renderiza la verificación MFA o redirige al panel.
    """
    ip = get_client_ip(request)
    if not request.user.is_authenticated:
        return redirect('login')
    if not mfa.verificacion_pendiente(request):
        return redirect('dashboard_redirect')

    user = request.user
    contexto = {'email_enmascarado': mfa.enmascarar_email(user.email)}

    if request.method == 'POST' and request.POST.get('action') == 'reenviar':
        espera = mfa.segundos_para_reenviar(request)
        if espera:
            contexto['error'] = f'Espere {espera} segundos para solicitar un nuevo código.'
        elif mfa.enviar_codigo(request, user):
            contexto['info'] = 'Enviamos un nuevo código a su correo.'
            AuditLog.objects.create(user=user, action="MFA_CODE_RESENT", ip_address=ip,
                                    details="Reenvío del código iToken por correo.")
        else:
            contexto['error'] = 'No se pudo enviar el código. Verifique su correo o contacte al administrador.'

    elif request.method == 'POST':
        resultado = mfa.verificar_codigo(request, user, request.POST.get('itoken_code'))
        if resultado == mfa.VALIDO:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.itoken_verified = True
            profile.save(update_fields=['itoken_verified'])
            AuditLog.objects.create(user=user, action="MFA_SUCCESS", ip_address=ip,
                                    details="Verificación de iToken por correo exitosa.")
            return redirect('dashboard_redirect')

        mensajes = {
            mfa.INVALIDO: f'iToken inválido. Le quedan {mfa.intentos_restantes(request)} intentos.',
            mfa.EXPIRADO: 'El código venció. Solicite uno nuevo.',
            mfa.BLOQUEADO: 'Demasiados intentos fallidos. Solicite un nuevo código.',
            mfa.SIN_CODIGO: 'No hay un código vigente. Solicite uno nuevo.',
        }
        contexto['error'] = mensajes[resultado]
        AuditLog.objects.create(user=user, action="MFA_FAILED", ip_address=ip,
                                details=f"Código iToken incorrecto ({resultado}).")

    elif not user.email:
        contexto['error'] = 'Su usuario no tiene correo registrado. Contacte al administrador.'

    return render(request, 'authentication/mfa_verify.html', contexto)

def _estado_interfaz_seguro(request):
    """
    Estado inicial de la interfaz para el dashboard; si falla, el dashboard se muestra igual
    (sin actualización automática) en lugar de dar error.

    Args:
        request (HttpRequest): Solicitud actual.

    Returns:
        dict | None: Estado de la interfaz, o None si no se pudo calcular.
    """
    try:
        return estado_interfaz(request)
    except Exception:
        logger.exception("No se pudo calcular el estado de la interfaz para la actualización automática")
        return None


@login_required
def estado_interfaz_api(request):
    """
    API JSON con la firma del estado de la interfaz del usuario.

    Los dashboards la consultan cada 30 segundos y se recargan si la firma cambió
    (notificaciones nuevas, solicitudes de anulación pendientes o cotizaciones actualizadas).

    Args:
        request (HttpRequest): Solicitud GET de un usuario autenticado.

    Returns:
        JsonResponse: Estado de la interfaz (ver ``authentication.estado_interfaz.estado_interfaz``).
    """
    return JsonResponse(estado_interfaz(request))


@login_required
def dashboard_redirect_view(request):
    """
    Redirige al usuario a su panel personalizado según su rol asignado,
    incluyendo la pizarra de cotizaciones en tiempo real y tasas personalizadas según categoría (PSE-9).
    
    Args:
        request (HttpRequest): Objeto de petición HTTP de Django.
        
    Returns:
        HttpResponse: Renderiza la plantilla del panel correspondiente al rol con tasas y beneficios.
    """
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'mark_read':
            notif_id = request.POST.get('notification_id')
            try:
                from tasas_cambio.models import NotificationLog
                from tasas_cambio.views import get_active_client, notificaciones_visibles_q
                active_client = get_active_client(request.user, request=request)
                visibles = NotificationLog.objects.filter(notificaciones_visibles_q(request.user, active_client))
                if notif_id:
                    visibles.filter(id=notif_id).update(is_read=True)
                else:
                    visibles.filter(is_read=False).update(is_read=True)
            except Exception:
                pass
            return redirect('dashboard_redirect')

    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    interface_ctx = get_user_interface_context(request, profile)

    default_rates = [
        ('USD', 'Dólar Estadounidense', Decimal('7300.0000'), Decimal('7450.0000')),
        ('EUR', 'Euro', Decimal('7900.0000'), Decimal('8150.0000')),
        ('BRL', 'Real Brasileño', Decimal('1350.0000'), Decimal('1450.0000')),
        ('ARS', 'Peso Argentino', Decimal('7.5000'), Decimal('9.0000')),
        ('PYG', 'Guaraní Paraguayo', Decimal('1.0000'), Decimal('1.0000')),
    ]

    for code, name, buy, sell in default_rates:
        ExchangeRate.objects.get_or_create(
            currency_code=code,
            defaults={
                'currency_name': name,
                'buy_rate': buy,
                'sell_rate': sell
            }
        )

    rates = ExchangeRate.objects.all().order_by('id')

    has_client_mode = bool(interface_ctx.get('active_client') or interface_ctx.get('clientes_asociados') or interface_ctx['active_group'] or interface_ctx['corporate_groups'] or profile.is_corporate)
    
    if has_client_mode:
        if interface_ctx.get('active_client'):
            category = interface_ctx['active_client'].categoria
        else:
            category = profile.category
        try:
            from tasas_cambio.views import ensure_default_benefit_rules
            from tasas_cambio.models import ClientBenefitRule
            ensure_default_benefit_rules()
            rule = ClientBenefitRule.objects.filter(category_code=category).first()
            if rule:
                benefit_percentage = rule.benefit_percentage
                benefit_label = f"{benefit_percentage}% ({rule.category_name})"
                category_display = rule.category_name
            else:
                benefit_percentage = Decimal('0.00')
                benefit_label = 'Estándar'
                category_display = category
        except Exception:
            benefit_percentage = Decimal('0.00')
            benefit_label = 'Estándar'
            category_display = category
    else:
        benefit_percentage = Decimal('0.00')
        benefit_label = 'Sin Beneficio'
        category_display = 'Usuario Regular (Sin Grupo de Cliente)'

    personalized_rates = []
    for rate in rates:
        if benefit_percentage > 0 and rate.currency_code != 'PYG':
            factor = Decimal('1.00') - (benefit_percentage / Decimal('100.00'))
            custom_sell = (rate.sell_rate * factor).quantize(Decimal('0.0001'))
            custom_buy = (rate.buy_rate / factor).quantize(Decimal('0.0001'))
        else:
            custom_sell = rate.sell_rate
            custom_buy = rate.buy_rate

        personalized_rates.append({
            'currency_code': rate.currency_code,
            'currency_name': rate.currency_name,
            'standard_buy': rate.buy_rate,
            'standard_sell': rate.sell_rate,
            'custom_buy': custom_buy,
            'custom_sell': custom_sell,
            'last_updated': rate.last_updated,
        })

    user_mode = request.session.get('user_mode', False)

    try:
        from tasas_cambio.models import CurrencyAlert, NotificationLog
        active_client = interface_ctx.get('active_client')
        active_alerts_count = CurrencyAlert.objects.filter(cliente=active_client, is_active=True).count() if active_client else 0
        from tasas_cambio.views import notificaciones_visibles_q
        unread_qs = NotificationLog.objects.filter(notificaciones_visibles_q(request.user, active_client), is_read=False)
        unread_notifications = unread_qs.order_by('-created_at')[:5]
        unread_notifications_count = unread_qs.count()
    except Exception:
        active_alerts_count = 0
        unread_notifications = []
        unread_notifications_count = 0

    active_turn_cajero = None
    cajas_cajero = []
    try:
        from caja.models import TurnoCaja, Caja
        active_turn_cajero = TurnoCaja.objects.filter(cajero=request.user, estado='ABIERTO').first()
        if not active_turn_cajero:
            cajas_cajero = Caja.objects.filter(activa=True, cajeros_asignados=request.user)
    except Exception:
        pass

    solicitudes_anulacion_pendientes = 0
    if tiene_permiso(request, 'can_manage_invoices'):
        from procesamiento_operaciones.models import SolicitudAnulacionFactura
        solicitudes_anulacion_pendientes = SolicitudAnulacionFactura.objects.filter(
            estado=SolicitudAnulacionFactura.PENDIENTE
        ).count()

    context = {
        'profile': profile,
        'solicitudes_anulacion_pendientes': solicitudes_anulacion_pendientes,
        'rates': personalized_rates,
        'benefit_percentage': benefit_percentage,
        'benefit_label': benefit_label,
        'category_display': category_display,
        'active_alerts_count': active_alerts_count,
        'unread_notifications': unread_notifications,
        'unread_notifications_count': unread_notifications_count,
        'active_turn_cajero': active_turn_cajero,
        'cajas_cajero': cajas_cajero,
        'now': timezone.now(),
        'estado_interfaz': _estado_interfaz_seguro(request),
        'intervalo_auto_actualizacion': INTERVALO_AUTO_ACTUALIZACION,
        **interface_ctx,
    }

    if interface_ctx['is_admin']:
        return render(request, 'authentication/admin_dashboard.html', context)
    elif profile.role and 'cajero' in profile.role.name.lower():
        return render(request, 'authentication/cajero_dashboard.html', context)
    elif profile.role and 'analista' in profile.role.name.lower():
        return render(request, 'authentication/analista_dashboard.html', context)
    # "Corporativo" es una categoría de beneficio del cliente (como VIP), no una interfaz aparte:
    # los clientes corporativos usan el panel de cliente.
    elif has_client_mode:
        if user_mode:
            return render(request, 'authentication/user_dashboard.html', context)
        return render(request, 'authentication/client_dashboard.html', context)
    else:
        return render(request, 'authentication/user_dashboard.html', context)

@login_required
def switch_to_user_mode_view(request):
    """
    Permite al usuario cambiar a la interfaz de usuario regular (sin cliente).
    """
    ip = get_client_ip(request)
    request.session['user_mode'] = True
    AuditLog.objects.create(
        user=request.user,
        action="SWITCH_TO_USER_MODE",
        ip_address=ip,
        details=f"Usuario {request.user.username} cambió a la interfaz de usuario regular (sin cliente)."
    )
    return redirect('dashboard_redirect')

@login_required
def switch_to_client_mode_view(request):
    """
    Permite al usuario regresar a la interfaz de cliente.
    """
    ip = get_client_ip(request)
    request.session['user_mode'] = False
    AuditLog.objects.create(
        user=request.user,
        action="SWITCH_TO_CLIENT_MODE",
        ip_address=ip,
        details=f"Usuario {request.user.username} regresó a la interfaz de cliente."
    )
    return redirect('dashboard_redirect')

def logout_view(request):
    """
    Cierra la sesión local en Django y la sesión SSO en Keycloak.
    
    Args:
        request (HttpRequest): Objeto de petición HTTP de Django.
        
    Returns:
        HttpResponseRedirect: Redirección al endpoint de cierre de sesión de Keycloak.
    """
    if request.user.is_authenticated:
        AuditLog.objects.create(user=request.user, action="LOGOUT", ip_address=get_client_ip(request), details="Cierre de sesión.")
    
    logout(request)
    
    redirect_uri = request.build_absolute_uri('/auth/login/')
    keycloak_logout_url = f"{getattr(settings, 'KEYCLOAK_FRONTEND_URL', settings.KEYCLOAK_SERVER_URL)}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/logout?client_id={settings.KEYCLOAK_CLIENT_ID}&post_logout_redirect_uri={redirect_uri}"
    
    return redirect(keycloak_logout_url)

@login_required
def admin_roles_view(request):
    """
    Vista de administración para gestionar la descripción y asignación granular de permisos
    de los roles fijos del sistema: Admin, Analista y Cliente (PSE-26).
    
    Args:
        request (HttpRequest): Objeto de petición HTTP de Django.
        
    Returns:
        HttpResponse: Renderiza la plantilla de gestión de roles y permisos con control RBAC.
    """
    if not tiene_permiso(request, 'can_manage_roles'):
        AuditLog.objects.create(
            user=request.user,
            action="RBAC_ACCESS_DENIED",
            ip_address=get_client_ip(request),
            details="Intento de acceso no autorizado al módulo de gestión de roles (PSE-26)."
        )
        return render(request, 'authentication/admin_roles.html', {
            'roles': Role.objects.none(),
            'permissions': Permission.objects.none(),
            'error': 'Acceso denegado: Se requiere el permiso «Gestionar Roles y Permisos».'
        })

    role_names = [ROL_ADMIN, ROL_ANALISTA, ROL_CLIENTE, ROL_CAJERO, ROL_JEFE, ROL_OPERADOR, ROL_ANALISTA_CORP]
    descriptions = {
        ROL_ADMIN: 'Administrador General (Acceso Total - Protegido)',
        ROL_ANALISTA: 'Analista de Operaciones (Permisos Delegados)',
        ROL_CLIENTE: 'Cliente de la Plataforma (Física y Jurídica General)',
        ROL_CAJERO: 'Cajero de Sucursal (Operaciones de Caja)',
        ROL_JEFE: 'Jefe / Propietario de Grupo Corporativo (Jurídica)',
        ROL_OPERADOR: 'Operador dentro del Grupo Corporativo (Jurídica)',
        ROL_ANALISTA_CORP: 'Analista dentro del Grupo Corporativo (Jurídica)',
    }
    for rname in role_names:
        Role.objects.get_or_create(name=rname, defaults={'description': descriptions.get(rname, '')})

    asegurar_catalogo_permisos()

    # Los roles que el administrador todavía no configuró muestran (y guardan) sus permisos por defecto
    for role in Role.objects.filter(name__in=role_names, permisos_configurados=False):
        role.permissions.set(Permission.objects.filter(codename__in=permisos_del_rol(role.name)))
        role.permisos_configurados = True
        role.save(update_fields=['permisos_configurados'])

    def pantalla(error=None):
        """Renderiza la pantalla de roles, opcionalmente con un error."""
        ctx = {
            'roles': Role.objects.filter(name__in=role_names).prefetch_related('permissions').order_by('id'),
            'permissions': Permission.objects.filter(codename__in=TODOS_LOS_PERMISOS).order_by('id'),
            'now': timezone.now(),
        }
        if error:
            ctx['error'] = error
        return render(request, 'authentication/admin_roles.html', ctx)

    ip = get_client_ip(request)

    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'edit':
            role_id = request.POST.get('role_id')
            role = get_object_or_404(Role, id=role_id)
            
            if es_rol_admin(role.name):
                return pantalla('El rol Administrador está protegido y no pueden modificarse sus permisos ni descripción.')

            if rol_efectivo(request) == role.name:
                return pantalla('No puedes modificar los permisos de tu propio rol. Solicítalo a un Administrador.')

            description = request.POST.get('description', '').strip()
            permission_ids = request.POST.getlist('permissions')
            selected_perms = Permission.objects.filter(id__in=permission_ids)
            selected_codes = set(selected_perms.values_list('codename', flat=True))

            if role.name in ROLES_DE_CLIENTE or role.name.lower() == 'cliente':
                administrativos = selected_codes & PERMISOS_ADMINISTRATIVOS
                if administrativos:
                    nombres = ', '.join(sorted(NOMBRES_PERMISOS.get(c, c) for c in administrativos))
                    return pantalla(f'Error de validación: El rol {role.name} no puede tener asignados permisos administrativos ({nombres}).')

            if role.name.lower() == 'analista' and 'can_perform_exchange' in selected_codes:
                return pantalla('Error de validación: El rol Analista no realiza transacciones de cliente.')

            role.description = description
            role.permisos_configurados = True
            role.save()
            role.permissions.set(selected_perms)

            try:
                token_url = f"{settings.KEYCLOAK_SERVER_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/token"
                token_data = {
                    'grant_type': 'client_credentials',
                    'client_id': settings.KEYCLOAK_CLIENT_ID,
                    'client_secret': getattr(settings, 'KEYCLOAK_CLIENT_SECRET', ''),
                }
                token_resp = requests.post(token_url, data=token_data, timeout=3)
                if token_resp.status_code == 200:
                    admin_token = token_resp.json().get('access_token')
                    kc_role_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/roles/{role.name}"
                    headers = {'Authorization': f'Bearer {admin_token}', 'Content-Type': 'application/json'}
                    requests.put(kc_role_url, json={'name': role.name, 'description': description}, headers=headers, timeout=3)
            except Exception:
                pass

            AuditLog.objects.create(
                user=request.user,
                action="ROLE_UPDATE",
                ip_address=ip,
                details=f"Modificación del rol fijo {role.name}: permisos {', '.join(sorted(selected_codes)) or 'ninguno'}."
            )
            messages.success(request, f"Permisos del rol {role.name} actualizados.")
            return redirect('admin_roles')

    return pantalla()

@login_required
@requiere_permiso('can_view_audit')
def admin_audit_logs_view(request):
    """
    Vista para consultar el registro de auditoría del sistema (PSE-26).
    Requiere el permiso «Ver Auditoría» (por defecto: Admin y Analista).
    """
    logs = AuditLog.objects.all().order_by('-timestamp')
    context = {
        'logs': logs,
        'now': timezone.now(),
    }
    return render(request, 'authentication/admin_audit_logs.html', context)


class SifenLoginAPIView(APIView):
    """
    API view para autenticarse en el servicio SIFEN (Factura Segura) y obtener el token.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        email = request.data.get('email')
        password = request.data.get('password')
        token = FacturaSeguraService.obtener_token(email, password)
        if token:
            return Response({"authentication_token": token}, status=status.HTTP_200_OK)
        return Response({"error": "No se pudo autenticar con Factura Segura."}, status=status.HTTP_400_BAD_REQUEST)


class SifenGenerarDEAPIView(APIView):
    """
    API view para generar Documento Electrónico (DE) en SIFEN a partir de una transacción.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        auth_token = request.headers.get('Authentication-Token') or request.data.get('authentication_token')
        if not auth_token:
            return Response({"error": "Token de autenticación de SIFEN requerido."}, status=status.HTTP_400_BAD_REQUEST)
        
        transaccion_data = request.data.get('transaccion_data', request.data)
        resultado = FacturaSeguraService.generar_de(auth_token, transaccion_data)
        return Response(resultado, status=status.HTTP_200_OK)


class SifenEstadoAPIView(APIView):
    """
    API view para consultar el estado de un Documento Electrónico en SIFEN (get_estado_sifen).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        auth_token = request.headers.get('Authentication-Token') or request.data.get('authentication_token')
        cdc = request.data.get('cdc')
        d_ruc_em = request.data.get('dRucEm')
        
        if not auth_token or not cdc or not d_ruc_em:
            return Response({"error": "Parámetros Authentication-Token, CDC y dRucEm son requeridos."}, status=status.HTTP_400_BAD_REQUEST)
        
        resultado = FacturaSeguraService.get_estado_sifen(auth_token, cdc, d_ruc_em)
        return Response(resultado, status=status.HTTP_200_OK)


class SifenDownloadKudeAPIView(APIView):
    """
    API view para descargar el KuDE (PDF oficial) de SIFEN.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, cdc):
        auth_token = request.headers.get('Authentication-Token') or request.query_params.get('authentication_token')
        if not auth_token:
            auth_token = FacturaSeguraService.obtener_token()
        if not auth_token:
            return Response({"error": "Token de autenticación de SIFEN requerido."}, status=status.HTTP_400_BAD_REQUEST)
        
        pdf_content = FacturaSeguraService.descargar_kude(auth_token, cdc)
        if pdf_content:
            response = HttpResponse(pdf_content, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="kude_{cdc}.pdf"'
            return response
        return Response({"error": "No se pudo descargar el KuDE oficial desde SIFEN."}, status=status.HTTP_404_NOT_FOUND)


class SifenDownloadXmlAPIView(APIView):
    """
    API view para descargar el XML firmado de SIFEN.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, cdc):
        auth_token = request.headers.get('Authentication-Token') or request.query_params.get('authentication_token')
        if not auth_token:
            auth_token = FacturaSeguraService.obtener_token()
        if not auth_token:
            return Response({"error": "Token de autenticación de SIFEN requerido."}, status=status.HTTP_400_BAD_REQUEST)
        
        xml_content = FacturaSeguraService.descargar_xml(auth_token, cdc)
        if xml_content:
            response = HttpResponse(xml_content, content_type='application/xml')
            response['Content-Disposition'] = f'attachment; filename="de_{cdc}.xml"'
            return response
        return Response({"error": "No se pudo descargar el XML."}, status=status.HTTP_404_NOT_FOUND)
