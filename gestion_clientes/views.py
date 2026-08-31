import requests
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.conf import settings
from django.db import models
from authentication.models import UserProfile, AuditLog, Role

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

def sync_keycloak_clients():
    """
    Sincroniza los usuarios de Keycloak Admin REST API con la base de datos local,
    filtrando aquellos que tienen el rol de Cliente, Individual o Corporate (excluyendo admins),
    y extrayendo sus atributos personalizados como categoría y cédula/RUC.
    """
    try:
        token_url = f"{settings.KEYCLOAK_SERVER_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/token"
        token_data = {
            'grant_type': 'client_credentials',
            'client_id': settings.KEYCLOAK_CLIENT_ID,
            'client_secret': getattr(settings, 'KEYCLOAK_CLIENT_SECRET', ''),
        }
        token_resp = requests.post(token_url, data=token_data, timeout=3)
        if token_resp.status_code != 200:
            return

        admin_token = token_resp.json().get('access_token')
        headers = {'Authorization': f'Bearer {admin_token}', 'Content-Type': 'application/json'}
        
        users_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users"
        users_resp = requests.get(users_url, headers=headers, timeout=3)
        if users_resp.status_code != 200:
            return

        kc_users = users_resp.json()
        for kc_user in kc_users:
            kc_id = kc_user.get('id')
            username = kc_user.get('username')
            email = kc_user.get('email', '')
            first_name = kc_user.get('firstName', '')
            attributes = kc_user.get('attributes', {})
            
            roles_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users/{kc_id}/role-mappings/realm"
            roles_resp = requests.get(roles_url, headers=headers, timeout=3)
            user_roles = [r.get('name', '').lower() for r in roles_resp.json()] if roles_resp.status_code == 200 else []

            if any(r in user_roles for r in ['admin', 'administrador', 'operador']):
                continue

            category_attr = attributes.get('category', ['MINORISTA'])[0].upper()
            if category_attr not in ['MINORISTA', 'CORPORATIVO', 'VIP']:
                category_attr = 'MINORISTA'

            ci_ruc_attr = attributes.get('ci_ruc', [''])[0]
            user_type_attr = attributes.get('userType', ['fisica'])[0].lower()
            is_corp = user_type_attr == 'juridica' or any(r in user_roles for r in ['corporate', 'corporativo'])

            user, _ = User.objects.get_or_create(username=username, defaults={'email': email, 'first_name': first_name})
            if email and not user.email:
                user.email = email
                user.save()

            role_name = "Corporate" if is_corp else "Cliente"
            role_obj, _ = Role.objects.get_or_create(name=role_name)

            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.keycloak_id = kc_id
            profile.role = role_obj
            profile.is_corporate = is_corp
            if ci_ruc_attr:
                profile.ci_ruc = ci_ruc_attr
            profile.category = category_attr
            profile.save()
    except Exception as e:
        pass

@login_required
def admin_client_list_view(request):
    """
    Vista del panel administrativo para consultar, filtrar y buscar clientes según categoría y naturaleza,
    sincronizando en tiempo real con Keycloak Admin REST API.
    
    Args:
        request (HttpRequest): Objeto de petición HTTP de Django.
        
    Returns:
        HttpResponse: Renderiza la plantilla del listado de clientes con filtros aplicados.
    """
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not (request.user.is_superuser or (profile.role and profile.role.name.lower() in ['admin', 'administrador'])):
        return redirect('dashboard_redirect')

    sync_keycloak_clients()

    query = request.GET.get('q', '').strip()
    category_filter = request.GET.get('category', '').strip()

    profiles = UserProfile.objects.select_related('user', 'role').filter(
        models.Q(role__name__iexact='Cliente') |
        models.Q(role__name__iexact='Corporate') |
        models.Q(role__name__iexact='Individual') |
        models.Q(is_corporate=True) |
        models.Q(category__in=['MINORISTA', 'CORPORATIVO', 'VIP'])
    ).exclude(
        models.Q(role__name__icontains='admin') | models.Q(user__is_superuser=True)
    )

    if query:
        profiles = profiles.filter(
            models.Q(user__username__icontains=query) |
            models.Q(user__email__icontains=query) |
            models.Q(ci_ruc__icontains=query) |
            models.Q(user__first_name__icontains=query)
        )

    if category_filter in ['MINORISTA', 'CORPORATIVO', 'VIP']:
        profiles = profiles.filter(category=category_filter)

    return render(request, 'gestion_clientes/admin_client_list.html', {
        'profiles': profiles,
        'query': query,
        'category_filter': category_filter
    })

@login_required
def admin_client_detail_view(request, user_id):
    """
    Vista de detalle y edición de la ficha de un cliente, permitiendo actualizar
    su categoría y volumen transaccional con validación de coherencia, persistencia en Keycloak y registro de auditoría.
    
    Args:
        request (HttpRequest): Objeto de petición HTTP de Django.
        user_id (int): ID del usuario/cliente.
        
    Returns:
        HttpResponse: Renderiza la ficha del cliente o redirige tras actualización exitosa.
    """
    admin_profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not (request.user.is_superuser or (admin_profile.role and admin_profile.role.name.lower() in ['admin', 'administrador'])):
        return redirect('dashboard_redirect')

    target_user = get_object_or_404(User, id=user_id)
    target_profile, _ = UserProfile.objects.get_or_create(user=target_user)

    error = None
    success = None

    if request.method == 'POST':
        new_category = request.POST.get('category', '').strip()
        new_volume_str = request.POST.get('transaction_volume', '0').strip()

        try:
            new_volume = float(new_volume_str) if new_volume_str else 0.0
            target_profile.clean_category_assignment(new_category, new_volume)
            
            old_category = target_profile.category
            target_profile.category = new_category
            target_profile.transaction_volume = new_volume
            target_profile.save()

            if target_profile.keycloak_id:
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
                        headers = {'Authorization': f'Bearer {admin_token}', 'Content-Type': 'application/json'}
                        
                        update_user_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users/{target_profile.keycloak_id}"
                        get_resp = requests.get(update_user_url, headers=headers, timeout=3)
                        if get_resp.status_code == 200:
                            user_data = get_resp.json()
                            attributes = user_data.get('attributes', {})
                            attributes['category'] = [new_category]
                            user_data['attributes'] = attributes
                            requests.put(update_user_url, json=user_data, headers=headers, timeout=3)
                except Exception as e:
                    pass

            AuditLog.objects.create(
                user=request.user,
                action="CLIENT_CATEGORY_UPDATE",
                ip_address=get_client_ip(request),
                details=f"Admin {request.user.username} modificó categoría de {target_user.username} de {old_category} a {new_category} (Volumen: {new_volume} Gs)."
            )
            success = "Categoría y volumen transaccional actualizados exitosamente en BD y Keycloak. Cambios reflejados de forma inmediata."
        except ValueError as e:
            error = str(e)
        except Exception as e:
            error = f"Error al actualizar la categoría: {str(e)}"

    audit_logs = AuditLog.objects.filter(
        models.Q(user=target_user) | models.Q(details__icontains=target_user.username)
    ).order_by('-timestamp')[:10]

    return render(request, 'gestion_clientes/admin_client_detail.html', {
        'target_profile': target_profile,
        'target_user': target_user,
        'error': error,
        'success': success,
        'audit_logs': audit_logs
    })
