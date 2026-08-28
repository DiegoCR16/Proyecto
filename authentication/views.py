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
    Vista de inicio de sesión. Permite login tradicional o redirección al SSO de Keycloak.
    """
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        ip = get_client_ip(request)

        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            AuditLog.objects.create(
                user=user,
                action="LOGIN_SUCCESS",
                ip_address=ip,
                details="Inicio de sesión exitoso por credenciales locales."
            )
            # Verificar requerimiento de MFA / iToken
            profile, created = UserProfile.objects.get_or_create(user=user)
            if profile.requires_mfa() and not profile.itoken_verified:
                return redirect('mfa_verify')
            return redirect('dashboard_redirect')
        else:
            AuditLog.objects.create(
                user=None,
                action="LOGIN_FAILED",
                ip_address=ip,
                details=f"Intento fallido para usuario: {username}"
            )
            return render(request, 'authentication/login.html', {'error': 'Credenciales inválidas.'})

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

    # Simulación de intercambio de token y obtención de usuario desde Keycloak
    # En producción real, se hace POST al token endpoint de Keycloak
    from django.contrib.auth.models import User
    username = "keycloak_user"
    user, created = User.objects.get_or_create(username=username, defaults={'email': 'sso@globalexchange.com'})
    profile, p_created = UserProfile.objects.get_or_create(user=user, defaults={'is_corporate': True})

    login(request, user)
    AuditLog.objects.create(
        user=user,
        action="SSO_LOGIN_SUCCESS",
        ip_address=ip,
        details="Inicio de sesión SSO Keycloak exitoso."
    )

    if profile.requires_mfa() and not profile.itoken_verified:
        return redirect('mfa_verify')
    return redirect('dashboard_redirect')

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
    """Cierra la sesión del usuario actual."""
    if request.user.is_authenticated:
        AuditLog.objects.create(user=request.user, action="LOGOUT", ip_address=get_client_ip(request), details="Cierre de sesión.")
    logout(request)
    return redirect('login')
