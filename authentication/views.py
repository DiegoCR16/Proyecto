import requests
import base64
import json
import re
from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.views.decorators.csrf import ensure_csrf_cookie
from django.conf import settings
from django.http import HttpResponseBadRequest
from .models import UserProfile, AuditLog, Role

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
    Vista de inicio de sesión principal. Redirige o presenta el acceso a Keycloak SSO.
    
    Args:
        request (HttpRequest): Objeto de petición HTTP de Django.
        
    Returns:
        HttpResponse: Respuesta renderizada con la plantilla de login.
    """
    return render(request, 'authentication/login.html')

def keycloak_login_redirect(request):
    """
    Redirige al servidor de Keycloak para iniciar el flujo OIDC / SSO de autenticación.
    
    Args:
        request (HttpRequest): Objeto de petición HTTP de Django.
        
    Returns:
        HttpResponseRedirect: Redirección al endpoint de autenticación de Keycloak.
    """
    keycloak_url = f"{settings.KEYCLOAK_SERVER_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/auth"
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
    keycloak_reg_url = f"{settings.KEYCLOAK_SERVER_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/registrations"
    params = f"?client_id={settings.KEYCLOAK_CLIENT_ID}&redirect_uri={settings.KEYCLOAK_REDIRECT_URI}&response_type=code&scope=openid"
    return redirect(keycloak_reg_url + params)

@ensure_csrf_cookie
def register_view(request):
    """
    Vista para el registro de clientes Personas Físicas (PSE-2).
    Valida campos obligatorios (nombre completo, cédula/RUC, correo, contraseña),
    control de duplicados y redirecciona al IdP (Keycloak) o completa el registro.
    
    Args:
        request (HttpRequest): Objeto de petición HTTP de Django.
        
    Returns:
        HttpResponse: Renderiza la plantilla de registro con errores o redirige a Keycloak.
    """
    ip = get_client_ip(request)
    if request.method == 'POST':
        full_name = request.POST.get('full_name', '').strip()
        ci_ruc = request.POST.get('ci_ruc', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '').strip()

        # Validación 1: Campos obligatorios
        if not full_name or not ci_ruc or not email or not password:
            return render(request, 'authentication/register.html', {
                'error': 'Todos los campos obligatorios deben ser completados.',
                'full_name': full_name,
                'ci_ruc': ci_ruc,
                'email': email
            })

        # Validación 2: Nombre completo solo alfabético
        if not re.match(r'^[A-Za-záéíóúÁÉÍÓÚñÑ\s]+$', full_name):
            return render(request, 'authentication/register.html', {
                'error': 'El nombre completo debe contener únicamente caracteres alfabéticos.',
                'full_name': full_name,
                'ci_ruc': ci_ruc,
                'email': email
            })

        # Validación 3: Cédula o RUC formato numérico válido
        if not re.match(r'^\d{1,8}(-\d)?$', ci_ruc):
            return render(request, 'authentication/register.html', {
                'error': 'El número de cédula o RUC debe tener un formato numérico válido.',
                'full_name': full_name,
                'ci_ruc': ci_ruc,
                'email': email
            })

        # Validación 4: Correo electrónico (máscara texto@dominio.extensión)
        if not re.match(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$', email):
            return render(request, 'authentication/register.html', {
                'error': 'El correo electrónico no cumple con la máscara texto@dominio.extensión.',
                'full_name': full_name,
                'ci_ruc': ci_ruc,
                'email': email
            })

        # Validación 5: Seguridad de contraseña (min 8 caracteres, mayús, min, carac. especial)
        if len(password) < 8 or not re.search(r'[A-Z]', password) or not re.search(r'[a-z]', password) or not re.search(r'[\W_]', password):
            return render(request, 'authentication/register.html', {
                'error': 'La contraseña debe tener un mínimo de 8 caracteres e incluir mayúsculas, minúsculas y caracteres especiales.',
                'full_name': full_name,
                'ci_ruc': ci_ruc,
                'email': email
            })

        # Validación 6: Control de Duplicados
        if User.objects.filter(email=email).exists() or UserProfile.objects.filter(ci_ruc=ci_ruc).exists():
            AuditLog.objects.create(
                action="REGISTER_DUPLICATE_ATTEMPT",
                ip_address=ip,
                details=f"Intento de registro duplicado para email: {email} o CI/RUC: {ci_ruc}"
            )
            return render(request, 'authentication/register.html', {
                'error': 'El correo electrónico o número de cédula/RUC ya se encuentra registrado.',
                'full_name': full_name,
                'ci_ruc': ci_ruc,
                'email': email
            })

        # Registro exitoso o redirección a Keycloak de registro
        AuditLog.objects.create(
            action="REGISTER_VALIDATION_SUCCESS",
            ip_address=ip,
            details=f"Validación exitosa para registro de Persona Física: {email}"
        )
        return redirect('keycloak_register')

    return render(request, 'authentication/register.html')

@ensure_csrf_cookie
def keycloak_callback_view(request):
    """
    Callback del SSO de Keycloak tras autenticación exitosa.
    Intercambia el código por tokens y extrae usuario y roles desde Keycloak.
    
    Args:
        request (HttpRequest): Objeto de petición HTTP de Django.
        
    Returns:
        HttpResponse: Redirección al panel del usuario o respuesta de error.
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

        userinfo_url = f"{settings.KEYCLOAK_SERVER_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/userinfo"
        userinfo_resp = requests.get(userinfo_url, headers={'Authorization': f'Bearer {access_token}'})
        userinfo = userinfo_resp.json()

        username = userinfo.get('preferred_username', 'keycloak_user')
        email = userinfo.get('email', '')

        token_parts = access_token.split('.')
        payload_encoded = token_parts[1]
        payload_encoded += '=' * (-len(payload_encoded) % 4)
        payload_json = json.loads(base64.urlsafe_b64decode(payload_encoded).decode('utf-8'))
        
        realm_roles = payload_json.get('realm_access', {}).get('roles', [])
        client_roles = payload_json.get('resource_access', {}).get(settings.KEYCLOAK_CLIENT_ID, {}).get('roles', [])
        all_roles = list(set(realm_roles + client_roles))
        all_roles_lower = [r.lower() for r in all_roles]

        user, created = User.objects.get_or_create(username=username, defaults={'email': email})
        
        role_obj = None
        is_corp = False
        
        if any(r in all_roles_lower for r in ['admin', 'administrador', 'administrator', 'operador']):
            role_obj, _ = Role.objects.get_or_create(name="Admin")
        elif any(r in all_roles_lower for r in ['corporate', 'corporativo', 'empresa']):
            role_obj, _ = Role.objects.get_or_create(name="Corporate")
            is_corp = True
        else:
            role_obj, _ = Role.objects.get_or_create(name="Individual")

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = role_obj
        profile.is_corporate = is_corp
        profile.save()

        login(request, user)
        AuditLog.objects.create(
            user=user,
            action="SSO_LOGIN_SUCCESS",
            ip_address=ip,
            details=f"Inicio de sesión SSO exitoso. Roles Keycloak: {all_roles}"
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
    Redirige al usuario a su panel personalizado según su rol asignado.
    
    Args:
        request (HttpRequest): Objeto de petición HTTP de Django.
        
    Returns:
        HttpResponse: Renderiza la plantilla del panel correspondiente al rol.
    """
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    role_name = profile.role.name.lower() if profile.role else 'individual'

    if 'admin' in role_name or request.user.is_superuser:
        return render(request, 'authentication/admin_dashboard.html', {'profile': profile})
    elif profile.is_corporate or 'corporativo' in role_name:
        return render(request, 'authentication/corporate_dashboard.html', {'profile': profile})
    else:
        return render(request, 'authentication/client_dashboard.html', {'profile': profile})

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
    keycloak_logout_url = f"{settings.KEYCLOAK_SERVER_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/logout?client_id={settings.KEYCLOAK_CLIENT_ID}&post_logout_redirect_uri={redirect_uri}"
    
    return redirect(keycloak_logout_url)
