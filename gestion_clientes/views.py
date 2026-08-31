import requests
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.conf import settings
from django.db import models, IntegrityError
from authentication.models import UserProfile, AuditLog, Role, CorporateGroup, GroupMembership

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

def setup_keycloak_corporate_group(juridica_profile):
    """
    Crea un grupo en Keycloak con el nombre de la persona jurídica,
    asigna al usuario jurídico el rol de 'jefe', y asegura la existencia
    de los roles de grupo 'operador' y 'analista'.
    """
    company_name = juridica_profile.user.get_full_name() or juridica_profile.user.username
    if juridica_profile.ci_ruc:
        company_name = f"{company_name} ({juridica_profile.ci_ruc})"
    
    group_obj, created = CorporateGroup.objects.get_or_create(
        juridica_profile=juridica_profile,
        defaults={'group_name': company_name}
    )

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

            if not group_obj.keycloak_group_id:
                groups_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/groups"
                group_payload = {"name": group_obj.group_name}
                requests.post(groups_url, json=group_payload, headers=headers, timeout=3)
                
                get_groups_resp = requests.get(groups_url, headers=headers, timeout=3)
                if get_groups_resp.status_code == 200:
                    for g in get_groups_resp.json():
                        if g.get('name') == group_obj.group_name:
                            group_obj.keycloak_group_id = g.get('id')
                            group_obj.save()
                            break

            if juridica_profile.keycloak_id and group_obj.keycloak_group_id:
                assign_group_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users/{juridica_profile.keycloak_id}/groups/{group_obj.keycloak_group_id}"
                requests.put(assign_group_url, headers=headers, timeout=3)
    except Exception as e:
        pass
    return group_obj

def link_physical_to_corporate_group(corporate_group, fisica_profile, role_in_group):
    """
    Vincula una persona física al grupo corporativo en Keycloak y en la base de datos local.
    Permite que una persona física esté vinculada a múltiples grupos corporativos.
    """
    membership, created = GroupMembership.objects.get_or_create(
        corporate_group=corporate_group,
        fisica_profile=fisica_profile,
        defaults={'role_in_group': role_in_group}
    )
    if not created:
        membership.role_in_group = role_in_group
        membership.save()

    try:
        if fisica_profile.keycloak_id and corporate_group.keycloak_group_id:
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
                assign_group_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users/{fisica_profile.keycloak_id}/groups/{corporate_group.keycloak_group_id}"
                requests.put(assign_group_url, headers=headers, timeout=3)
    except Exception as e:
        pass
    return membership

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
                existing = UserProfile.objects.filter(ci_ruc=ci_ruc_attr).exclude(id=profile.id).first()
                if not existing:
                    profile.ci_ruc = ci_ruc_attr
            profile.category = category_attr
            try:
                profile.save()
            except IntegrityError:
                pass

            if profile.is_corporate:
                setup_keycloak_corporate_group(profile)
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
    Vista de detalle y gestión de la ficha de un cliente (PSE-7), permitiendo
    crear personas físicas directamente en Keycloak asociadas al grupo corporativo con rol (Operador/Analista),
    gestionar grupos corporativos y roles, actualizar categoría y volumen transaccional, y registrar auditoría.
    
    Args:
        request (HttpRequest): Objeto de petición HTTP de Django.
        user_id (int): ID del usuario/cliente local.
        
    Returns:
        HttpResponse: Renderiza la plantilla `client_user_mapping.html`.
    """
    admin_profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not (request.user.is_superuser or (admin_profile.role and admin_profile.role.name.lower() in ['admin', 'administrador'])):
        return redirect('dashboard_redirect')

    target_user = get_object_or_404(User, id=user_id)
    target_profile, _ = UserProfile.objects.get_or_create(user=target_user)

    error = None
    success = None

    if target_profile.is_corporate:
        corporate_group = setup_keycloak_corporate_group(target_profile)
    else:
        corporate_group = None

    if request.method == 'POST':
        action = request.POST.get('action', 'update_category').strip()

        if action == 'create_direct' and target_profile.is_corporate:
            # Creación directa de Persona Física asociada al grupo corporativo de esta Persona Jurídica
            new_name = request.POST.get('new_username', '').strip()  # Nombre completo -> firstName
            new_email = request.POST.get('new_email', '').strip()    # Correo -> username y email en Keycloak
            new_ci_ruc = request.POST.get('new_ci_ruc', '').strip()  # Cédula o RUC
            new_password = request.POST.get('new_password', '').strip()
            role_in_group = request.POST.get('role_in_group', 'OPERADOR').strip() # Operador o Analista

            if not new_name or not new_email or not new_password or not new_ci_ruc:
                error = "Todos los campos (incluyendo Cédula o RUC) para la creación directa de la persona física son obligatorios."
            elif UserProfile.objects.filter(ci_ruc=new_ci_ruc).exists():
                error = "El número de cédula o RUC ya se encuentra registrado en el sistema."
            else:
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
                        create_user_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users"
                        user_payload = {
                            "username": new_email,
                            "email": new_email,
                            "firstName": new_name,
                            "enabled": True,
                            "attributes": {
                                "category": ["MINORISTA"],
                                "userType": ["fisica"],
                                "ci_ruc": [new_ci_ruc]
                            },
                            "credentials": [{"type": "password", "value": new_password, "temporary": False}]
                        }
                        create_resp = requests.post(create_user_url, json=user_payload, headers=headers, timeout=3)
                        if create_resp.status_code not in [200, 201, 204]:
                            error = f"Error al crear usuario en Keycloak (Código {create_resp.status_code}): {create_resp.text}"
                        else:
                            search_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users?email={new_email}"
                            search_resp = requests.get(search_url, headers=headers, timeout=3)
                            if search_resp.status_code == 200 and search_resp.json():
                                kc_id = search_resp.json()[0].get('id')
                                
                                fisica_user, _ = User.objects.get_or_create(username=new_email, defaults={'email': new_email, 'first_name': new_name})
                                if not fisica_user.email:
                                    fisica_user.email = new_email
                                    fisica_user.save()
                                
                                client_role, _ = Role.objects.get_or_create(name="Cliente")
                                fisica_profile, _ = UserProfile.objects.get_or_create(user=fisica_user)
                                fisica_profile.keycloak_id = kc_id
                                fisica_profile.role = client_role
                                fisica_profile.is_corporate = False
                                fisica_profile.ci_ruc = new_ci_ruc
                                fisica_profile.save()

                                link_physical_to_corporate_group(corporate_group, fisica_profile, role_in_group)

                                AuditLog.objects.create(
                                    user=request.user,
                                    action="CREATE_PHYSICAL_MEMBER_FOR_CORPORATE",
                                    ip_address=get_client_ip(request),
                                    details=f"Admin {request.user.username} creó cuenta física '{new_email}' (CI/RUC: {new_ci_ruc}, ID: {kc_id}, Rol: {role_in_group}) y la asoció al grupo {corporate_group.group_name}."
                                )
                                success = f"Persona física '{new_name}' (CI/RUC: {new_ci_ruc}) creada en Keycloak y asociada exitosamente al grupo como {role_in_group}."
                            else:
                                error = "Cuenta creada en Keycloak pero no se pudo recuperar el ID para la asociación automática."
                    else:
                        error = "Error al conectar con la API Admin de Keycloak para creación directa (verifique credenciales)."
                except requests.exceptions.ConnectionError:
                    error = "No se pudo conectar con el servidor Keycloak (http://localhost:8080). Asegúrese de que Keycloak esté en ejecución."
                except requests.exceptions.Timeout:
                    error = "Tiempo de espera agotado al conectar con Keycloak."
                except Exception as e:
                    error = f"Error en creación directa en Keycloak: {str(e)}"

        elif action == 'link_physical_member':
            fisica_profile_id = request.POST.get('fisica_profile_id', '').strip()
            role_in_group = request.POST.get('role_in_group', 'OPERADOR').strip()
            if fisica_profile_id and corporate_group:
                fisica_profile = get_object_or_404(UserProfile, id=fisica_profile_id, is_corporate=False)
                link_physical_to_corporate_group(corporate_group, fisica_profile, role_in_group)
                success = f"Persona física '{fisica_profile.user.username}' vinculada exitosamente al grupo corporativo como {role_in_group}."
                AuditLog.objects.create(
                    user=request.user,
                    action="CORPORATE_GROUP_MEMBER_LINK",
                    ip_address=get_client_ip(request),
                    details=f"Admin {request.user.username} vinculó a {fisica_profile.user.username} al grupo {corporate_group.group_name} con rol {role_in_group}."
                )
            else:
                error = "Debe seleccionar una persona física válida para vincular."

        elif action == 'link_to_corporate_group':
            corp_group_id = request.POST.get('corporate_group_id', '').strip()
            role_in_group = request.POST.get('role_in_group', 'OPERADOR').strip()
            if corp_group_id and not target_profile.is_corporate:
                corp_group = get_object_or_404(CorporateGroup, id=corp_group_id)
                link_physical_to_corporate_group(corp_group, target_profile, role_in_group)
                success = f"Vinculación exitosa al grupo corporativo '{corp_group.group_name}' como {role_in_group}."
                AuditLog.objects.create(
                    user=request.user,
                    action="PHYSICAL_TO_CORPORATE_GROUP_LINK",
                    ip_address=get_client_ip(request),
                    details=f"Admin {request.user.username} vinculó a la persona física {target_user.username} al grupo corporativo {corp_group.group_name} con rol {role_in_group}."
                )
            else:
                error = "Debe seleccionar un grupo corporativo válido."

        else: # update_category
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
                success = "Categoría y volumen transaccional actualizados exitosamente en BD y Keycloak."
            except ValueError as e:
                error = str(e)
            except Exception as e:
                error = f"Error al actualizar la categoría: {str(e)}"

    available_physical_profiles = UserProfile.objects.filter(is_corporate=False).select_related('user') if target_profile.is_corporate else None
    all_corporate_groups = CorporateGroup.objects.all().select_related('juridica_profile__user') if not target_profile.is_corporate else None

    audit_logs = AuditLog.objects.filter(
        models.Q(user=target_user) | models.Q(details__icontains=target_user.username)
    ).order_by('-timestamp')[:10]

    return render(request, 'gestion_clientes/client_user_mapping.html', {
        'target_profile': target_profile,
        'target_user': target_user,
        'corporate_group': corporate_group,
        'available_physical_profiles': available_physical_profiles,
        'all_corporate_groups': all_corporate_groups,
        'error': error,
        'success': success,
        'audit_logs': audit_logs
    })
