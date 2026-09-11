import requests
import base64
import json
import re
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.views.decorators.csrf import ensure_csrf_cookie
from django.conf import settings
from django.http import HttpResponseBadRequest
from django.db import models
from decimal import Decimal
from django.utils import timezone
from .models import UserProfile, AuditLog, Role, Permission, CorporateGroup, GroupMembership, ClientRegistrationRequest, MemberRequest
from tasas_cambio.models import ExchangeRate
from gestion_clientes.views import get_user_interface_context

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

        user, created = User.objects.get_or_create(username=username, defaults={'email': email})
        if email and not user.email:
            user.email = email
            user.save()

        role_obj = None
        is_corp = False
        
        if any(r in all_roles_lower for r in ['admin', 'administrador', 'administrator']):
            role_obj, _ = Role.objects.get_or_create(name="Admin")
        elif any(r in all_roles_lower for r in ['cajero']):
            role_obj, _ = Role.objects.get_or_create(name="Cajero")
        elif any(r in all_roles_lower for r in ['analista']):
            role_obj, _ = Role.objects.get_or_create(name="Analista")
        elif any(r in all_roles_lower for r in ['corporate', 'corporativo', 'empresa', 'jefe']):
            role_obj, _ = Role.objects.get_or_create(name="Corporate")
            is_corp = True
        else:
            role_obj, _ = Role.objects.get_or_create(name="Cliente")

        profile, _ = UserProfile.objects.get_or_create(user=user)
        if kc_id:
            profile.keycloak_id = kc_id
        profile.role = role_obj
        profile.is_corporate = is_corp
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

        if profile.requires_mfa() and not profile.itoken_verified:
            return redirect('mfa_verify')
        return redirect('dashboard_redirect')

    except Exception as e:
        return HttpResponseBadRequest(f"Error en comunicación con Keycloak: {str(e)}")

@ensure_csrf_cookie
def mfa_verify_view(request):
    """
    Vista para la verificación de iToken / MFA obligatorio para administrativos y corporativos.
    
    Args:
        request (HttpRequest): Objeto de petición HTTP de Django.
        
    Returns:
        HttpResponse: Renderiza la verificación MFA o redirige al panel.
    """
    ip = get_client_ip(request)
    if not request.user.is_authenticated:
        return redirect('login')

    if request.method == 'POST':
        itoken_code = request.POST.get('itoken_code')
        if itoken_code == '123456':
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            profile.itoken_verified = True
            profile.save()
            AuditLog.objects.create(
                user=request.user,
                action="MFA_SUCCESS",
                ip_address=ip,
                details="Verificación de iToken/MFA exitosa."
            )
            return redirect('dashboard_redirect')
        else:
            AuditLog.objects.create(
                user=request.user,
                action="MFA_FAILED",
                ip_address=ip,
                details="Código iToken/MFA incorrecto."
            )
            return render(request, 'authentication/mfa_verify.html', {'error': 'iToken inválido. Use 123456 para pruebas.'})

    return render(request, 'authentication/mfa_verify.html')

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
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    role_name = profile.role.name.lower() if profile.role else 'individual'

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

    has_client_mode = interface_ctx['active_group'] or profile.is_corporate or (profile.role and profile.role.name.lower() in ['cliente', 'corporate', 'corporativo', 'vip'])
    
    if has_client_mode:
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

    context = {
        'profile': profile,
        'rates': personalized_rates,
        'benefit_percentage': benefit_percentage,
        'benefit_label': benefit_label,
        'category_display': category_display,
        'now': timezone.now(),
        **interface_ctx,
    }

    if interface_ctx['is_admin']:
        return render(request, 'authentication/admin_dashboard.html', context)
    elif profile.role and 'cajero' in profile.role.name.lower():
        return render(request, 'authentication/cajero_dashboard.html', context)
    elif profile.role and 'analista' in profile.role.name.lower():
        return render(request, 'authentication/analista_dashboard.html', context)
    elif profile.is_corporate or 'corporativo' in role_name:
        return render(request, 'authentication/corporate_dashboard.html', context)
    else:
        return render(request, 'authentication/client_dashboard.html', context)

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
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    role_name = profile.role.name.lower() if profile.role else ''
    if not (request.user.is_superuser or 'admin' in role_name or 'administrador' in role_name):
        AuditLog.objects.create(
            user=request.user,
            action="RBAC_ACCESS_DENIED",
            ip_address=get_client_ip(request),
            details="Intento de acceso no autorizado al módulo de gestión de roles (PSE-26)."
        )
        return render(request, 'authentication/admin_roles.html', {
            'roles': Role.objects.none(),
            'permissions': Permission.objects.none(),
            'error': 'Acceso denegado: Se requiere rol de Administrador para gestionar roles y permisos.'
        })

    role_names = ['Admin', 'Analista', 'Cliente', 'Cajero', 'Jefe (Corporativo)', 'Operador (Corporativo)', 'Analista (Corporativo)']
    descriptions = {
        'Admin': 'Administrador General (Acceso Total - Protegido)',
        'Analista': 'Analista de Operaciones (Permisos Delegados)',
        'Cliente': 'Cliente de la Plataforma (Física y Jurídica General)',
        'Cajero': 'Cajero de Sucursal (Operaciones de Caja)',
        'Jefe (Corporativo)': 'Jefe / Propietario de Grupo Corporativo (Jurídica)',
        'Operador (Corporativo)': 'Operador dentro del Grupo Corporativo (Jurídica)',
        'Analista (Corporativo)': 'Analista dentro del Grupo Corporativo (Jurídica)',
    }
    for rname in role_names:
        Role.objects.get_or_create(name=rname, defaults={'description': descriptions.get(rname, '')})

    default_perms = [
        ('can_manage_roles', 'Gestionar Roles y Permisos', 'Crear, editar, desactivar roles y asignar permisos (Admin/Analista)'),
        ('can_manage_clients', 'Gestionar Clientes', 'Crear, editar y asociar cuentas de clientes y grupos corporativos (Admin/Analista)'),
        ('can_manage_rates', 'Gestionar Tasas de Cambio', 'Actualizar cotizaciones y tasas de cambio en tiempo real (Admin/Analista)'),
        ('can_view_audit', 'Ver Auditoría', 'Consultar logs de auditoría e incidentes de seguridad (Admin/Analista)'),
        ('can_perform_exchange', 'Realizar Transacciones', 'Ejecutar compra y venta de divisas en la interfaz de cliente'),
        ('can_view_rates', 'Ver Pizarra de Tasas', 'Consultar cotizaciones actualizadas en tiempo real'),
    ]
    for codename, name, desc in default_perms:
        Permission.objects.get_or_create(codename=codename, defaults={'name': name, 'description': desc})

    ip = get_client_ip(request)

    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'edit':
            role_id = request.POST.get('role_id')
            role = get_object_or_404(Role, id=role_id)
            
            if role.name.lower() in ['admin', 'administrador']:
                return render(request, 'authentication/admin_roles.html', {
                    'roles': Role.objects.filter(name__in=role_names).prefetch_related('permissions'),
                    'permissions': Permission.objects.all(),
                    'error': 'El rol Administrador está protegido y no pueden modificarse sus permisos ni descripción.'
                })

            description = request.POST.get('description', '').strip()
            permission_ids = request.POST.getlist('permissions')

            if role.name.lower() == 'cliente':
                admin_codenames = ['can_manage_roles', 'can_manage_clients', 'can_manage_rates', 'can_view_audit']
                selected_perms = Permission.objects.filter(id__in=permission_ids)
                if selected_perms.filter(codename__in=admin_codenames).exists():
                    return render(request, 'authentication/admin_roles.html', {
                        'roles': Role.objects.filter(name__in=role_names).prefetch_related('permissions'),
                        'permissions': Permission.objects.all(),
                        'error': 'Error de validación: El rol Cliente no puede tener asignados permisos administrativos.'
                    })

            if role.name.lower() == 'analista':
                selected_perms = Permission.objects.filter(id__in=permission_ids)
                if selected_perms.filter(codename='can_perform_exchange').exists():
                    return render(request, 'authentication/admin_roles.html', {
                        'roles': Role.objects.filter(name__in=role_names).prefetch_related('permissions'),
                        'permissions': Permission.objects.all(),
                        'error': 'Error de validación: El rol Analista no realiza transacciones de cliente.'
                    })

            role.description = description
            role.save()
            role.permissions.set(Permission.objects.filter(id__in=permission_ids))

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
            except Exception as e:
                pass

            AuditLog.objects.create(
                user=request.user,
                action="ROLE_UPDATE",
                ip_address=ip,
                details=f"Modificación del rol fijo {role.name}."
            )
            return redirect('admin_roles')

    roles = Role.objects.filter(name__in=role_names).prefetch_related('permissions').order_by('id')
    permissions = Permission.objects.all()

    context = {
        'roles': roles,
        'permissions': permissions,
        'now': timezone.now(),
    }
    return render(request, 'authentication/admin_roles.html', context)

@login_required
def admin_audit_logs_view(request):
    """
    Vista de administración para consultar el registro de auditoría del sistema (PSE-26).
    """
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    role_name = profile.role.name.lower() if profile.role else ''
    if not (request.user.is_superuser or 'admin' in role_name or 'administrador' in role_name):
        AuditLog.objects.create(
            user=request.user,
            action="RBAC_ACCESS_DENIED",
            ip_address=get_client_ip(request),
            details="Intento de acceso no autorizado al módulo de auditoría."
        )
        return render(request, 'authentication/admin_dashboard.html', {
            'error': 'Acceso denegado: Se requiere rol de Administrador.'
        })

    logs = AuditLog.objects.all().order_by('-timestamp')
    context = {
        'logs': logs,
        'now': timezone.now(),
    }
    return render(request, 'authentication/admin_audit_logs.html', context)
