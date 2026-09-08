import requests
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.conf import settings
from django.db import models, IntegrityError
from authentication.models import UserProfile, AuditLog, Role, CorporateGroup, GroupMembership, ClientRegistrationRequest, MemberRequest

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

            if group_obj.keycloak_group_id:
                roles_to_create = ['CLIENTE', 'OPERADOR', 'ANALISTA']
                roles_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/roles"
                for rname in roles_to_create:
                    full_role_name = f"{group_obj.group_name}_{rname}"
                    role_payload = {"name": full_role_name, "description": f"Rol {rname} para el grupo {group_obj.group_name}"}
                    requests.post(roles_url, json=role_payload, headers=headers, timeout=3)

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

            role_name = "Cliente"
            role_obj, _ = Role.objects.get_or_create(name=role_name)

            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.keycloak_id = kc_id
            profile.role = role_obj
            profile.is_corporate = is_corp
            if ci_ruc_attr:
                existing = UserProfile.objects.filter(ci_ruc=ci_ruc_attr).exclude(id=profile.id).first()
                if not existing:
                    profile.ci_ruc = ci_ruc_attr
            profile.category = 'CORPORATIVO' if is_corp else category_attr
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
    Vista del panel administrativo para consultar, filtrar y buscar clientes según categoría y naturaleza.
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

    pending_client_requests = ClientRegistrationRequest.objects.filter(status='PENDING').select_related('user', 'corporate_group')
    pending_member_requests = MemberRequest.objects.filter(status='PENDING').select_related('corporate_group', 'requester')

    return render(request, 'gestion_clientes/admin_client_list.html', {
        'profiles': profiles,
        'query': query,
        'category_filter': category_filter,
        'pending_client_requests': pending_client_requests,
        'pending_member_requests': pending_member_requests,
    })

@login_required
def admin_client_detail_view(request, user_id):
    """
    Vista de detalle y gestión de la ficha de un cliente (PSE-7).
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
            new_name = request.POST.get('new_username', '').strip()
            new_email = request.POST.get('new_email', '').strip()
            new_ci_ruc = request.POST.get('new_ci_ruc', '').strip()
            new_password = request.POST.get('new_password', '').strip()
            role_in_group = request.POST.get('role_in_group', 'OPERADOR').strip()

            if not new_name or not new_email or not new_password or not new_ci_ruc:
                error = "Todos los campos para la creación directa son obligatorios."
            elif UserProfile.objects.filter(ci_ruc=new_ci_ruc).exists():
                error = "El número de cédula o RUC ya se encuentra registrado."
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
                            error = f"Error al crear usuario en Keycloak: {create_resp.text}"
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
                                    details=f"Admin {request.user.username} creó cuenta física '{new_email}' y la asoció al grupo {corporate_group.group_name} como {role_in_group}."
                                )
                                success = f"Cuenta creada y asociada exitosamente como {role_in_group}."
                            else:
                                error = "Cuenta creada en Keycloak pero no se pudo obtener el ID."
                    else:
                        error = "Error al conectar con la API de Keycloak."
                except Exception as e:
                    error = f"Error: {str(e)}"

        elif action == 'link_physical_member':
            fisica_profile_id = request.POST.get('fisica_profile_id', '').strip()
            role_in_group = request.POST.get('role_in_group', 'OPERADOR').strip()
            if fisica_profile_id and corporate_group:
                fisica_profile = get_object_or_404(UserProfile, id=fisica_profile_id, is_corporate=False)
                link_physical_to_corporate_group(corporate_group, fisica_profile, role_in_group)
                success = f"Persona física vinculada exitosamente como {role_in_group}."
                AuditLog.objects.create(
                    user=request.user,
                    action="CORPORATE_GROUP_MEMBER_LINK",
                    ip_address=get_client_ip(request),
                    details=f"Admin {request.user.username} vinculó a {fisica_profile.user.username} al grupo {corporate_group.group_name} con rol {role_in_group}."
                )

        elif action == 'link_to_corporate_group':
            corp_group_id = request.POST.get('corporate_group_id', '').strip()
            role_in_group = request.POST.get('role_in_group', 'OPERADOR').strip()
            if corp_group_id and not target_profile.is_corporate:
                corp_group = get_object_or_404(CorporateGroup, id=corp_group_id)
                link_physical_to_corporate_group(corp_group, target_profile, role_in_group)
                success = f"Vinculación exitosa al grupo '{corp_group.group_name}' como {role_in_group}."
                AuditLog.objects.create(
                    user=request.user,
                    action="PHYSICAL_TO_CORPORATE_GROUP_LINK",
                    ip_address=get_client_ip(request),
                    details=f"Admin {request.user.username} vinculó a {target_user.username} al grupo {corp_group.group_name} como {role_in_group}."
                )

        else:
            new_category = request.POST.get('category', '').strip()
            new_volume_str = request.POST.get('transaction_volume', '0').strip()

            try:
                new_volume = float(new_volume_str) if new_volume_str else 0.0
                target_profile.clean_category_assignment(new_category, new_volume)
                
                old_category = target_profile.category
                target_profile.category = new_category
                target_profile.transaction_volume = new_volume
                target_profile.save()

                AuditLog.objects.create(
                    user=request.user,
                    action="CLIENT_CATEGORY_UPDATE",
                    ip_address=get_client_ip(request),
                    details=f"Admin {request.user.username} modificó categoría de {target_user.username} de {old_category} a {new_category}."
                )
                success = "Categoría y volumen transaccional actualizados exitosamente."
            except ValueError as e:
                error = str(e)

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

@login_required
def register_view(request):
    """
    Vista para que un usuario solicite ser cliente (Persona Física o Jurídica).
    Crea un grupo pendiente de aprobación por el Administrador.
    """
    ip = get_client_ip(request)
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    
    if request.method == 'POST':
        client_name = request.POST.get('client_name', '').strip()
        ci_ruc = request.POST.get('ci_ruc', '').strip()
        client_type = request.POST.get('client_type', 'FISICA').strip()
        email = request.POST.get('email', '').strip()

        if not client_name or not ci_ruc or not email:
            return render(request, 'gestion_clientes/register.html', {
                'error': 'Todos los campos son obligatorios.',
                'client_name': client_name,
                'ci_ruc': ci_ruc,
                'client_type': client_type,
                'email': email
            })

        if ClientRegistrationRequest.objects.filter(ci_ruc=ci_ruc).exists():
            return render(request, 'gestion_clientes/register.html', {
                'error': 'Ya existe una solicitud de cliente con este número de Cédula o RUC.',
                'client_name': client_name,
                'ci_ruc': ci_ruc,
                'client_type': client_type,
                'email': email
            })

        try:
            ClientRegistrationRequest.objects.create(
                user=request.user,
                client_name=client_name,
                ci_ruc=ci_ruc,
                client_type=client_type,
                corporate_group=None,
                status='PENDING'
            )

            AuditLog.objects.create(
                user=request.user,
                action="CLIENT_REGISTRATION_REQUEST",
                ip_address=ip,
                details=f"Usuario {request.user.username} solicitó registro de cliente ({client_type}): {client_name} (CI/RUC: {ci_ruc})."
            )

            return render(request, 'gestion_clientes/register.html', {
                'success': 'Solicitud enviada exitosamente. El Administrador revisará y aprobará su grupo cliente.',
            })
        except Exception as e:
            return render(request, 'gestion_clientes/register.html', {
                'error': f'Error al procesar la solicitud: {str(e)}',
                'client_name': client_name,
                'ci_ruc': ci_ruc,
                'client_type': client_type,
                'email': email
            })

    return render(request, 'gestion_clientes/register.html')

@login_required
def request_member_view(request):
    """
    Permite a un cliente principal (dueño del grupo activo) solicitar asociar un usuario como Operador o Analista.
    """
    ip = get_client_ip(request)
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    
    interface_ctx = get_user_interface_context(request, profile)
    active_group = interface_ctx['active_group']

    if request.method == 'POST':
        target_email = request.POST.get('target_email', '').strip()
        role_requested = request.POST.get('role_requested', 'OPERADOR').strip()

        if not target_email or not active_group or active_group.juridica_profile != profile:
            return redirect('dashboard_redirect')

        MemberRequest.objects.create(
            corporate_group=active_group,
            requester=request.user,
            target_email=target_email,
            role_requested=role_requested,
            status='PENDING'
        )

        AuditLog.objects.create(
            user=request.user,
            action="MEMBER_ASSOCIATION_REQUEST",
            ip_address=ip,
            details=f"Cliente {request.user.username} solicitó asociar al correo {target_email} como {role_requested} al grupo {active_group.group_name}."
        )

    return redirect('dashboard_redirect')

@login_required
def admin_approve_client_request(request, request_id):
    """
    El Administrador aprueba una solicitud de creación de cliente, creando el grupo en Keycloak
    y asociando al usuario solicitante como 'Cliente' (JEFE/dueño del grupo).
    """
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not (request.user.is_superuser or (profile.role and 'admin' in profile.role.name.lower())):
        return redirect('dashboard_redirect')

    client_req = get_object_or_404(ClientRegistrationRequest, id=request_id)
    if client_req.status == 'PENDING':
        client_req.status = 'APPROVED'
        
        req_profile, _ = UserProfile.objects.get_or_create(user=client_req.user)
        group_name_full = f"{client_req.client_name} ({client_req.ci_ruc})"
        corp_group = CorporateGroup.objects.create(
            juridica_profile=req_profile,
            group_name=group_name_full
        )
        client_req.corporate_group = corp_group
        client_req.save()

        GroupMembership.objects.get_or_create(
            corporate_group=corp_group,
            fisica_profile=req_profile,
            defaults={'role_in_group': 'CLIENTE'}
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

                if not corp_group.keycloak_group_id:
                    groups_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/groups"
                    requests.post(groups_url, json={"name": corp_group.group_name}, headers=headers, timeout=3)
                    
                    get_groups_resp = requests.get(groups_url, headers=headers, timeout=3)
                    if get_groups_resp.status_code == 200:
                        for g in get_groups_resp.json():
                            if g.get('name') == corp_group.group_name:
                                corp_group.keycloak_group_id = g.get('id')
                                corp_group.save()
                                break

                if corp_group.keycloak_group_id:
                    roles_to_create = ['CLIENTE', 'OPERADOR', 'ANALISTA']
                    roles_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/roles"
                    for rname in roles_to_create:
                        full_role_name = f"{corp_group.group_name}_{rname}"
                        requests.post(roles_url, json={"name": full_role_name, "description": f"Rol {rname} para grupo {corp_group.group_name}"}, headers=headers, timeout=3)

                if req_profile.keycloak_id and corp_group.keycloak_group_id:
                    assign_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users/{req_profile.keycloak_id}/groups/{corp_group.keycloak_group_id}"
                    requests.put(assign_url, headers=headers, timeout=3)
        except Exception:
            pass

        AuditLog.objects.create(
            user=request.user,
            action="APPROVE_CLIENT_REQUEST",
            ip_address=get_client_ip(request),
            details=f"Admin {request.user.username} aprobó solicitud de cliente para {client_req.client_name}."
        )

    return redirect('admin_client_list')

@login_required
def admin_approve_member_request(request, request_id):
    """
    El Administrador aprueba una solicitud de asociación de miembro (Operador/Analista) a un grupo cliente.
    """
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not (request.user.is_superuser or (profile.role and 'admin' in profile.role.name.lower())):
        return redirect('dashboard_redirect')

    mem_req = get_object_or_404(MemberRequest, id=request_id)
    if mem_req.status == 'PENDING':
        mem_req.status = 'APPROVED'
        mem_req.save()

        target_user = User.objects.filter(email=mem_req.target_email).first() or User.objects.filter(username=mem_req.target_email).first()
        if target_user:
            target_profile, _ = UserProfile.objects.get_or_create(user=target_user)
            GroupMembership.objects.get_or_create(
                corporate_group=mem_req.corporate_group,
                fisica_profile=target_profile,
                defaults={'role_in_group': mem_req.role_requested}
            )
            try:
                if target_profile.keycloak_id and mem_req.corporate_group.keycloak_group_id:
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
                        assign_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users/{target_profile.keycloak_id}/groups/{mem_req.corporate_group.keycloak_group_id}"
                        requests.put(assign_url, headers=headers, timeout=3)
            except Exception:
                pass

        AuditLog.objects.create(
            user=request.user,
            action="APPROVE_MEMBER_REQUEST",
            ip_address=get_client_ip(request),
            details=f"Admin {request.user.username} aprobó asociación de {mem_req.target_email} como {mem_req.role_requested}."
        )

    return redirect('admin_client_list')

@login_required
def switch_group_view(request, group_id):
    """
    Permite al usuario cambiar activamente de grupo/cliente de Keycloak para operar dinámicamente.
    """
    ip = get_client_ip(request)
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    
    corp_group = CorporateGroup.objects.filter(keycloak_group_id=group_id).first()
    if not corp_group and str(group_id).isdigit():
        corp_group = CorporateGroup.objects.filter(id=int(group_id)).first()

    if corp_group:
        membership = GroupMembership.objects.filter(corporate_group=corp_group, fisica_profile=profile).first()
        if membership or profile.is_corporate or request.user.is_superuser:
            request.session['active_group_id'] = group_id
            AuditLog.objects.create(
                user=request.user,
                action="SWITCH_GROUP",
                ip_address=ip,
                details=f"Usuario {request.user.username} cambió al grupo/cliente activo: {corp_group.group_name}"
            )
    return redirect('dashboard_redirect')

def get_user_interface_context(request, profile):
    """
    Determina dinámicamente la insignia (badge), el rol activo, el grupo activo y las opciones de cambio de grupo,
    consultando a Keycloak como fuente principal de verdad para los grupos del usuario.
    """
    is_admin = request.user.is_superuser or (profile.role and 'admin' in profile.role.name.lower())
    
    corporate_groups = []
    if profile.keycloak_id:
        try:
            token_url = f"{settings.KEYCLOAK_SERVER_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/token"
            token_data = {
                'grant_type': 'client_credentials',
                'client_id': settings.KEYCLOAK_CLIENT_ID,
                'client_secret': getattr(settings, 'KEYCLOAK_CLIENT_SECRET', ''),
            }
            token_resp = requests.post(token_url, data=token_data, timeout=2)
            if token_resp.status_code == 200:
                admin_token = token_resp.json().get('access_token')
                headers = {'Authorization': f'Bearer {admin_token}', 'Content-Type': 'application/json'}
                groups_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users/{profile.keycloak_id}/groups"
                groups_resp = requests.get(groups_url, headers=headers, timeout=2)
                if groups_resp.status_code == 200:
                    for kc_g in groups_resp.json():
                        g_id = kc_g.get('id')
                        g_name = kc_g.get('name')
                        corp_group, _ = CorporateGroup.objects.get_or_create(
                            keycloak_group_id=g_id,
                            defaults={'group_name': g_name, 'juridica_profile': profile}
                        )
                        if corp_group not in corporate_groups:
                            corporate_groups.append(corp_group)
        except Exception:
            pass

    user_memberships = profile.group_memberships.select_related('corporate_group').all()
    for m in user_memberships:
        if m.corporate_group not in corporate_groups:
            corporate_groups.append(m.corporate_group)
    
    own_corp_group = CorporateGroup.objects.filter(juridica_profile=profile).first()
    if own_corp_group and own_corp_group not in corporate_groups:
        corporate_groups.append(own_corp_group)

    active_group_id = request.session.get('active_group_id')
    active_group = None
    active_membership = None

    if active_group_id:
        active_group = CorporateGroup.objects.filter(keycloak_group_id=active_group_id).first()
        if not active_group and str(active_group_id).isdigit():
            active_group = CorporateGroup.objects.filter(id=int(active_group_id)).first()
        if active_group:
            active_membership = GroupMembership.objects.filter(corporate_group=active_group, fisica_profile=profile).first()

    if not active_group and corporate_groups:
        active_group = corporate_groups[0]
        request.session['active_group_id'] = active_group.keycloak_group_id or str(active_group.id)
        active_membership = GroupMembership.objects.filter(corporate_group=active_group, fisica_profile=profile).first()

    badge_text = None
    if is_admin:
        badge_text = "Administrador"
    elif active_group:
        role_name = active_membership.role_in_group if active_membership else ("Corporativo" if profile.is_corporate else "Cliente")
        badge_text = f"{active_group.group_name} - {role_name}"
    elif profile.role and profile.role.name.lower() not in ['cliente', 'individual', '']:
        badge_text = profile.role.name
    else:
        badge_text = None

    has_own_group = CorporateGroup.objects.filter(juridica_profile=profile).exists()

    user_roles = list(profile.roles.all())
    if profile.role and profile.role not in user_roles:
        user_roles.append(profile.role)

    active_role_id = request.session.get('active_role_id')
    active_role = None
    if active_role_id:
        active_role = Role.objects.filter(id=active_role_id).first()
    if not active_role and user_roles:
        active_role = user_roles[0]
        request.session['active_role_id'] = active_role.id

    is_admin = request.user.is_superuser or (active_role and 'admin' in active_role.name.lower()) or (profile.role and 'admin' in profile.role.name.lower())

    is_group_owner = False
    if active_group and active_group.juridica_profile == profile:
        is_group_owner = True

    return {
        'is_admin': is_admin,
        'badge_text': badge_text,
        'corporate_groups': corporate_groups,
        'active_group': active_group,
        'active_membership': active_membership,
        'has_own_group': has_own_group,
        'user_roles': user_roles,
        'active_role': active_role,
        'is_group_owner': is_group_owner,
    }

@login_required
def admin_user_list_view(request):
    """
    CRUD de Usuarios: Listado de usuarios del sistema.
    """
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not (request.user.is_superuser or (profile.role and 'admin' in profile.role.name.lower())):
        return redirect('dashboard_redirect')
    
    interface_ctx = get_user_interface_context(request, profile)
    users = User.objects.all().select_related('profile__role').prefetch_related('profile__roles').order_by('id')
    ctx = {'users': users}
    ctx.update(interface_ctx)
    return render(request, 'gestion_clientes/admin_user_list.html', ctx)

@login_required
def admin_user_create_view(request):
    """
    CRUD de Usuarios: Creación de nuevo usuario y asignación de roles.
    """
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not (request.user.is_superuser or (profile.role and 'admin' in profile.role.name.lower())):
        return redirect('dashboard_redirect')
    
    interface_ctx = get_user_interface_context(request, profile)
    all_roles = Role.objects.filter(is_active=True)
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '').strip()
        role_id = request.POST.get('role_id', '').strip()
        additional_roles = request.POST.getlist('additional_roles')
        is_active = request.POST.get('is_active') == 'on'

        if not username or not email or not password:
            ctx = {
                'error': 'Todos los campos obligatorios deben completarse.',
                'all_roles': all_roles,
                'edit_mode': False
            }
            ctx.update(interface_ctx)
            return render(request, 'gestion_clientes/admin_user_form.html', ctx)

        if User.objects.filter(username=username).exists():
            ctx = {
                'error': 'El nombre de usuario ya existe.',
                'all_roles': all_roles,
                'edit_mode': False
            }
            ctx.update(interface_ctx)
            return render(request, 'gestion_clientes/admin_user_form.html', ctx)

        try:
            kc_id = None
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
                        "username": username,
                        "email": email,
                        "firstName": username,
                        "enabled": is_active,
                        "attributes": {
                            "category": ["MINORISTA"],
                            "userType": ["fisica"]
                        },
                        "credentials": [{"type": "password", "value": password, "temporary": False}]
                    }
                    create_resp = requests.post(create_user_url, json=user_payload, headers=headers, timeout=3)
                    if create_resp.status_code in [200, 201, 204]:
                        search_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/users?username={username}"
                        search_resp = requests.get(search_url, headers=headers, timeout=3)
                        if search_resp.status_code == 200 and search_resp.json():
                            kc_id = search_resp.json()[0].get('id')
            except Exception:
                pass

            new_user = User.objects.create_user(username=username, email=email, password=password, is_active=is_active)
            role_obj = Role.objects.filter(id=int(role_id)).first() if role_id else None
            new_profile = UserProfile.objects.create(user=new_user, role=role_obj, keycloak_id=kc_id)
            if additional_roles:
                new_profile.roles.set(Role.objects.filter(id__in=[int(rid) for rid in additional_roles]))

            AuditLog.objects.create(
                user=request.user,
                action="ADMIN_CREATE_USER",
                ip_address=get_client_ip(request),
                details=f"Administrador {request.user.username} creó el usuario {username} (Keycloak ID: {kc_id})."
            )
            return redirect('admin_user_list')
        except Exception as e:
            ctx = {
                'error': f'Error al crear usuario: {str(e)}',
                'all_roles': all_roles,
                'edit_mode': False
            }
            ctx.update(interface_ctx)
            return render(request, 'gestion_clientes/admin_user_form.html', ctx)

    ctx = {'all_roles': all_roles, 'edit_mode': False}
    ctx.update(interface_ctx)
    return render(request, 'gestion_clientes/admin_user_form.html', ctx)

@login_required
def admin_user_edit_view(request, user_id):
    """
    CRUD de Usuarios: Edición de cuenta de usuario y sus roles.
    """
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not (request.user.is_superuser or (profile.role and 'admin' in profile.role.name.lower())):
        return redirect('dashboard_redirect')
    
    interface_ctx = get_user_interface_context(request, profile)
    target_user = get_object_or_404(User, id=user_id)
    target_profile, _ = UserProfile.objects.get_or_create(user=target_user)
    all_roles = Role.objects.filter(is_active=True)

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '').strip()
        role_id = request.POST.get('role_id', '').strip()
        additional_roles = request.POST.getlist('additional_roles')
        is_active = request.POST.get('is_active') == 'on'

        if not username or not email:
            ctx = {
                'error': 'Nombre de usuario y correo son obligatorios.',
                'target_user': target_user,
                'all_roles': all_roles,
                'edit_mode': True
            }
            ctx.update(interface_ctx)
            return render(request, 'gestion_clientes/admin_user_form.html', ctx)

        try:
            target_user.username = username
            target_user.email = email
            target_user.is_active = is_active
            if password:
                target_user.set_password(password)
            target_user.save()

            role_obj = Role.objects.filter(id=int(role_id)).first() if role_id else None
            target_profile.role = role_obj
            target_profile.save()
            target_profile.roles.set(Role.objects.filter(id__in=[int(rid) for rid in additional_roles]))

            AuditLog.objects.create(
                user=request.user,
                action="ADMIN_EDIT_USER",
                ip_address=get_client_ip(request),
                details=f"Administrador {request.user.username} editó el usuario {username}."
            )
            return redirect('admin_user_list')
        except Exception as e:
            ctx = {
                'error': f'Error al editar usuario: {str(e)}',
                'target_user': target_user,
                'all_roles': all_roles,
                'edit_mode': True
            }
            ctx.update(interface_ctx)
            return render(request, 'gestion_clientes/admin_user_form.html', ctx)

    ctx = {'target_user': target_user, 'all_roles': all_roles, 'edit_mode': True}
    ctx.update(interface_ctx)
    return render(request, 'gestion_clientes/admin_user_form.html', ctx)

@login_required
def admin_user_delete_view(request, user_id):
    """
    CRUD de Usuarios: Desactivación / eliminación lógica de usuario.
    """
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not (request.user.is_superuser or (profile.role and 'admin' in profile.role.name.lower())):
        return redirect('dashboard_redirect')
    
    target_user = get_object_or_404(User, id=user_id)
    if target_user != request.user:
        target_user.is_active = False
        target_user.save()
        AuditLog.objects.create(
            user=request.user,
            action="ADMIN_DEACTIVATE_USER",
            ip_address=get_client_ip(request),
            details=f"Administrador {request.user.username} desactivó al usuario {target_user.username}."
        )
    return redirect('admin_user_list')

@login_required
def switch_role_view(request, role_id):
    """
    Permite al usuario cambiar dinámicamente de rol/interfaz activa (Admin, Cajero, Analista, etc.).
    """
    ip = get_client_ip(request)
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    role = Role.objects.filter(id=role_id).first()
    if role and (role == profile.role or role in profile.roles.all() or request.user.is_superuser):
        request.session['active_role_id'] = role.id
        AuditLog.objects.create(
            user=request.user,
            action="SWITCH_ROLE",
            ip_address=ip,
            details=f"Usuario {request.user.username} cambió a la interfaz del rol: {role.name}"
        )
    return redirect('dashboard_redirect')
