import requests
import base64
import json
from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import ensure_csrf_cookie
from django.conf import settings
from django.http import HttpResponseBadRequest
from .models import UserProfile, AuditLog, Role

def get_client_ip(request):
    """Obtiene la dirección IP del cliente desde la request."""
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
    """
    return render(request, 'authentication/login.html')

def keycloak_login_redirect(request):
    """
    Redirige al servidor de Keycloak para iniciar el flujo OIDC / SSO.
    """
    keycloak_url = f"{settings.KEYCLOAK_SERVER_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/auth"
    params = f"?client_id={settings.KEYCLOAK_CLIENT_ID}&redirect_uri={settings.KEYCLOAK_REDIRECT_URI}&response_type=code&scope=openid"
    return redirect(keycloak_url + params)

@ensure_csrf_cookie
def keycloak_callback_view(request):
    """
    Callback del SSO de Keycloak tras autenticación exitosa.
    Intercambia el código por tokens y extrae usuario y roles desde Keycloak.
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

        from django.contrib.auth.models import User
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
    """
    ip = get_client_ip(request)
    if not request.user.is_authenticated:
        return redirect('login')

    if request.method == 'POST':
        itoken_code = request.POST.get('itoken_code')
        # Validación de prueba: aceptamos '123456' como iToken válido
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
    """
    if request.user.is_authenticated:
        AuditLog.objects.create(user=request.user, action="LOGOUT", ip_address=get_client_ip(request), details="Cierre de sesión.")
    
    logout(request)
    
    # Redirigir al endpoint de logout de Keycloak para destruir la sesión SSO
    redirect_uri = request.build_absolute_uri('/auth/login/')
    keycloak_logout_url = f"{settings.KEYCLOAK_SERVER_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/logout?client_id={settings.KEYCLOAK_CLIENT_ID}&post_logout_redirect_uri={redirect_uri}"
    
    return redirect(keycloak_logout_url)
