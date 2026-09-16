import re
import requests
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.conf import settings
from django.db import models, IntegrityError
from authentication.models import UserProfile, AuditLog, Role, CorporateGroup, GroupMembership, ClientRegistrationRequest, MemberRequest, Cliente, UsuarioClienteRelacion

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
    Vista del panel administrativo para consultar, filtrar y buscar clientes (entidades de base de datos) según categoría y naturaleza.
    """
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not (request.user.is_superuser or (profile.role and profile.role.name.lower() in ['admin', 'administrador'])):
        return redirect('dashboard_redirect')

    error = None
    success = None

    if request.method == 'POST':
        action = request.POST.get('action', '').strip()
        if action == 'create_client':
            nombre = request.POST.get('nombre_o_razon_social', '').strip()
            doc = request.POST.get('documento_identidad', '').strip()
            tipo = request.POST.get('tipo_cliente', 'FISICA').strip()
            email = request.POST.get('email', '').strip()
            cat = request.POST.get('categoria', 'MINORISTA').strip()
            vol = request.POST.get('transaction_volume', '0').strip()

            if not nombre or not doc or not email:
                error = "Nombre, Cédula/RUC y Correo son obligatorios."
            elif Cliente.objects.filter(documento_identidad=doc).exists():
                error = "Ya existe un cliente con este número de Cédula o RUC."
            else:
                try:
                    vol_dec = Decimal(vol) if vol else Decimal('0.00')
                except Exception:
                    vol_dec = Decimal('0.00')

                Cliente.objects.create(
                    nombre_o_razon_social=nombre,
                    documento_identidad=doc,
                    tipo_cliente=tipo,
                    email=email,
                    categoria=cat,
                    transaction_volume=vol_dec
                )
                success = "Cliente creado exitosamente."
                AuditLog.objects.create(
                    user=request.user,
                    action="CREATE_CLIENT",
                    ip_address=get_client_ip(request),
                    details=f"Admin {request.user.username} creó el cliente {nombre} (Doc: {doc}, Cat: {cat})."
                )

    query = request.GET.get('q', '').strip()
    category_filter = request.GET.get('category', '').strip()

    clientes = Cliente.objects.all().order_by('-creado_en')

    if query:
        clientes = clientes.filter(
            models.Q(nombre_o_razon_social__icontains=query) |
            models.Q(email__icontains=query) |
            models.Q(documento_identidad__icontains=query)
        )

    if category_filter:
        clientes = clientes.filter(categoria__iexact=category_filter)

    pending_client_requests = ClientRegistrationRequest.objects.filter(status='PENDING').select_related('user', 'corporate_group')
    pending_member_requests = MemberRequest.objects.filter(status='PENDING').select_related('corporate_group', 'requester')

    return render(request, 'gestion_clientes/admin_client_list.html', {
        'clientes': clientes,
        'query': query,
        'category_filter': category_filter,
        'pending_client_requests': pending_client_requests,
        'pending_member_requests': pending_member_requests,
        'error': error,
        'success': success,
    })

@login_required
@login_required
def admin_client_detail_view(request, cliente_id):
    """
    Vista de detalle y gestión de la ficha de un cliente (basado en el modelo Cliente y grupo Keycloak).
    """
    admin_profile, _ = UserProfile.objects.get_or_create(user=request.user)
    if not (request.user.is_superuser or (admin_profile.role and admin_profile.role.name.lower() in ['admin', 'administrador'])):
        return redirect('dashboard_redirect')

    cliente = get_object_or_404(Cliente, id=cliente_id)
    corporate_group = CorporateGroup.objects.filter(group_name=cliente.nombre_o_razon_social).first()

    error = None
    success = None

    if request.method == 'POST':
        action = request.POST.get('action', '').strip()

        if action == 'update_category':
            new_cat = request.POST.get('category', '').strip()
            new_vol = request.POST.get('transaction_volume', '0').strip()
            if new_cat:
                cliente.categoria = new_cat
                try:
                    cliente.transaction_volume = Decimal(new_vol)
                except Exception:
                    pass
                cliente.save()
                success = "Categoría del cliente actualizada exitosamente."
                AuditLog.objects.create(
                    user=request.user,
                    action="UPDATE_CLIENT_CATEGORY",
                    ip_address=get_client_ip(request),
                    details=f"Admin {request.user.username} actualizó el cliente {cliente.nombre_o_razon_social}: Categoría={new_cat}, Volumen={cliente.transaction_volume} Gs."
                )

        elif action == 'update_client_info':
            nombre = request.POST.get('nombre_o_razon_social', '').strip()
            doc = request.POST.get('documento_identidad', '').strip()
            tipo = request.POST.get('tipo_cliente', 'FISICA').strip()
            email = request.POST.get('email', '').strip()
            cat = request.POST.get('category', '').strip()
            vol = request.POST.get('transaction_volume', '0').strip()

            if nombre:
                cliente.nombre_o_razon_social = nombre
            if doc:
                cliente.documento_identidad = doc
            if tipo:
                cliente.tipo_cliente = tipo
            if email:
                cliente.email = email
            if cat:
                cliente.categoria = cat
            try:
                cliente.transaction_volume = Decimal(vol) if vol else Decimal('0.00')
            except Exception:
                pass
            cliente.save()
            success = "Información del cliente actualizada exitosamente."
            AuditLog.objects.create(
                user=request.user,
                action="UPDATE_CLIENT",
                ip_address=get_client_ip(request),
                details=f"Admin {request.user.username} actualizó la información del cliente {cliente.nombre_o_razon_social}."
            )

        elif action == 'delete_client':
            nombre_cliente = cliente.nombre_o_razon_social
            cliente.delete()
            AuditLog.objects.create(
                user=request.user,
                action="DELETE_CLIENT",
                ip_address=get_client_ip(request),
                details=f"Admin {request.user.username} eliminó el cliente {nombre_cliente}."
            )
            return redirect('admin_client_list')

        elif action == 'create_user_and_associate':
            username = request.POST.get('new_username', '').strip()
            email = request.POST.get('new_email', '').strip()
            password = request.POST.get('new_password', '').strip()
            rol_en_cliente = request.POST.get('rol_en_cliente', 'OPERADOR').strip()

            if not username or not email or not password:
                error = "Todos los campos de usuario son obligatorios."
            else:
                is_valid_pw, pw_msg = validate_password_complexity(password)
                if not is_valid_pw:
                    error = pw_msg
                elif User.objects.filter(username=username).exists():
                    error = "El nombre de usuario ya existe."
                else:
                    try:
                        role_obj, _ = Role.objects.get_or_create(name="Cliente")
                        kc_id = sync_user_roles_to_keycloak(
                            username=username,
                            email=email,
                            password=password,
                            is_active=True,
                            role_obj=role_obj,
                            additional_roles=[],
                            existing_kc_id=None
                        )
                        new_user = User.objects.create_user(username=username, email=email, password=password)
                        new_profile = UserProfile.objects.create(user=new_user, role=role_obj, keycloak_id=kc_id)

                        UsuarioClienteRelacion.objects.get_or_create(
                            keycloak_user_id=kc_id or str(new_user.id),
                            cliente=cliente,
                            defaults={'rol_en_cliente': rol_en_cliente}
                        )
                        success = f"Usuario {username} creado y asociado exitosamente al cliente."
                        AuditLog.objects.create(
                            user=request.user,
                            action="CREATE_AND_ASSOCIATE_USER",
                            ip_address=get_client_ip(request),
                            details=f"Admin {request.user.username} creó el usuario {username} y lo asoció al cliente {cliente.nombre_o_razon_social} con rol {rol_en_cliente}."
                        )
                    except Exception as e:
                        error = f"Error al crear usuario: {str(e)}"

        elif action == 'associate_existing_user':
            user_id = request.POST.get('user_id', '').strip()
            rol_en_cliente = request.POST.get('rol_en_cliente', 'OPERADOR').strip()

            if user_id:
                target_user = User.objects.filter(id=user_id).first()
                if target_user:
                    target_profile, _ = UserProfile.objects.get_or_create(user=target_user)
                    kc_uid = target_profile.keycloak_id or str(target_user.id)
                    UsuarioClienteRelacion.objects.get_or_create(
                        keycloak_user_id=kc_uid,
                        cliente=cliente,
                        defaults={'rol_en_cliente': rol_en_cliente}
                    )
                    success = f"Usuario {target_user.username} asociado exitosamente al cliente."
                    AuditLog.objects.create(
                        user=request.user,
                        action="ASSOCIATE_EXISTING_USER",
                        ip_address=get_client_ip(request),
                        details=f"Admin {request.user.username} asoció al usuario existente {target_user.username} al cliente {cliente.nombre_o_razon_social} con rol {rol_en_cliente}."
                    )

        elif action == 'unassign_user':
            rel_id = request.POST.get('rel_id', '').strip()
            if rel_id:
                rel = UsuarioClienteRelacion.objects.filter(id=rel_id, cliente=cliente).first()
                if rel:
                    kc_uid = rel.keycloak_user_id
                    rel.delete()
                    success = "Asociación de usuario desasignada exitosamente."
                    AuditLog.objects.create(
                        user=request.user,
                        action="UNASSIGN_USER_FROM_CLIENT",
                        ip_address=get_client_ip(request),
                        details=f"Admin {request.user.username} desasignó al usuario (KC ID: {kc_uid}) del cliente {cliente.nombre_o_razon_social}."
                    )

    usuarios_relacionados = cliente.usuarios_relacionados.all()
    for rel in usuarios_relacionados:
        u = User.objects.filter(profile__keycloak_id=rel.keycloak_user_id).first() or User.objects.filter(id=rel.keycloak_user_id).first()
        rel.resolved_username = u.username if u else rel.keycloak_user_id
        rel.resolved_email = u.email if u else ""

    associated_kc_ids = list(usuarios_relacionados.values_list('keycloak_user_id', flat=True))
    available_users = User.objects.exclude(profile__keycloak_id__in=associated_kc_ids).select_related('profile')

    audit_logs = AuditLog.objects.filter(
        models.Q(details__icontains=cliente.nombre_o_razon_social) | 
        models.Q(details__icontains=cliente.documento_identidad)
    ).order_by('-timestamp')[:20]

    return render(request, 'gestion_clientes/admin_client_detail.html', {
        'cliente': cliente,
        'corporate_group': corporate_group,
        'usuarios_relacionados': usuarios_relacionados,
        'available_users': available_users,
        'audit_logs': audit_logs,
        'error': error,
        'success': success,
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
        group_name_full = client_req.client_name
        corp_group = CorporateGroup.objects.create(
            juridica_profile=req_profile,
            group_name=group_name_full
        )
        client_req.corporate_group = corp_group
        client_req.save()

        cliente_obj, _ = Cliente.objects.update_or_create(
            documento_identidad=client_req.ci_ruc,
            defaults={
                'nombre_o_razon_social': client_req.client_name,
                'tipo_cliente': client_req.client_type,
                'email': client_req.user.email or f"{client_req.ci_ruc}@globalexchange.com"
            }
        )
        if req_profile.keycloak_id:
            UsuarioClienteRelacion.objects.get_or_create(
                keycloak_user_id=req_profile.keycloak_id,
                cliente=cliente_obj,
                defaults={'rol_en_cliente': 'ADMIN'}
            )

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
                    # Guardar atributo ci_ruc en el grupo de Keycloak
                    update_group_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/groups/{corp_group.keycloak_group_id}"
                    requests.put(update_group_url, json={
                        "name": corp_group.group_name,
                        "attributes": {
                            "ci_ruc": [client_req.ci_ruc]
                        }
                    }, headers=headers, timeout=3)

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

@login_required
def switch_client_view(request, client_id):
    """
    Permite al usuario cambiar dinámicamente de cliente activo para reflejar su categoría y beneficios.
    """
    ip = get_client_ip(request)
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    
    cliente = Cliente.objects.filter(id=client_id).first()
    if not cliente and str(client_id).isdigit():
        cliente = Cliente.objects.filter(id=int(client_id)).first()

    if cliente:
        has_access = request.user.is_superuser or (profile.role and 'admin' in profile.role.name.lower())
        if not has_access and profile.keycloak_id:
            has_access = UsuarioClienteRelacion.objects.filter(keycloak_user_id=profile.keycloak_id, cliente=cliente).exists()

        if has_access:
            request.session['active_client_id'] = str(cliente.id)
            corp_group = CorporateGroup.objects.filter(group_name=cliente.nombre_o_razon_social).first()
            if corp_group:
                request.session['active_group_id'] = corp_group.keycloak_group_id or str(corp_group.id)

            AuditLog.objects.create(
                user=request.user,
                action="SWITCH_CLIENT",
                ip_address=ip,
                details=f"Usuario {request.user.username} cambió al cliente activo: {cliente.nombre_o_razon_social} (Categoría: {cliente.categoria})"
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

    clientes_asociados = []
    if profile.keycloak_id:
        relaciones = UsuarioClienteRelacion.objects.filter(keycloak_user_id=profile.keycloak_id).select_related('cliente')
        clientes_asociados = [rel.cliente for rel in relaciones]

    active_client_id = request.session.get('active_client_id')
    active_client = None
    if active_client_id:
        active_client = Cliente.objects.filter(id=active_client_id).first()
        if not active_client and str(active_client_id).isdigit():
            active_client = Cliente.objects.filter(id=int(active_client_id)).first()

    if not active_client and clientes_asociados:
        active_client = clientes_asociados[0]
        request.session['active_client_id'] = str(active_client.id)

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
        'clientes_asociados': clientes_asociados,
        'active_client': active_client,
    }

def validate_password_complexity(password):
    """
    Valida que la contraseña cumpla con los requisitos mínimos de seguridad:
    - Mínimo 8 caracteres.
    - Al menos una letra mayúscula.
    - Al menos un número.
    - Al menos un carácter especial.
    """
    if not password:
        return True, ""
    if len(password) < 8:
        return False, "La contraseña debe tener al menos 8 caracteres."
    if not re.search(r'[A-Z]', password):
        return False, "La contraseña debe contener al menos una letra mayúscula."
    if not re.search(r'\d', password):
        return False, "La contraseña debe contener al menos un número."
    if not re.search(r'[^A-Za-z0-9]', password):
        return False, "La contraseña debe contener al menos un carácter especial."
    return True, ""

def sync_user_roles_to_keycloak(username, email, password, is_active, role_obj, additional_roles, existing_kc_id=None):
    """
    Sincroniza un usuario y sus roles asignados con Keycloak Admin API.
    """
    try:
        token_url = f"{settings.KEYCLOAK_SERVER_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/token"
        token_data = {
            'grant_type': 'client_credentials',
            'client_id': settings.KEYCLOAK_CLIENT_ID,
            'client_secret': getattr(settings, 'KEYCLOAK_CLIENT_SECRET', ''),
        }
        token_resp = requests.post(token_url, data=token_data, timeout=5)
        if token_resp.status_code != 200:
            print(f"DEBUG KEYCLOAK TOKEN FAIL: {token_resp.status_code} - {token_resp.text}")
            return existing_kc_id

        admin_token = token_resp.json().get('access_token')
        headers = {'Authorization': f'Bearer {admin_token}', 'Content-Type': 'application/json'}
        base_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}"

        kc_id = existing_kc_id
        if not kc_id:
            search_url = f"{base_url}/users?username={username}"
            search_resp = requests.get(search_url, headers=headers, timeout=5)
            if search_resp.status_code == 200 and search_resp.json():
                kc_id = search_resp.json()[0].get('id')
            elif email:
                search_email_url = f"{base_url}/users?email={email}"
                search_email_resp = requests.get(search_email_url, headers=headers, timeout=5)
                if search_email_resp.status_code == 200 and search_email_resp.json():
                    kc_id = search_email_resp.json()[0].get('id')

        user_payload = {
            "username": username,
            "email": email,
            "firstName": username,
            "enabled": is_active,
            "attributes": {
                "category": ["MINORISTA"],
                "userType": ["fisica"]
            }
        }
        if password:
            user_payload["credentials"] = [{"type": "password", "value": password, "temporary": False}]

        if kc_id:
            put_resp = requests.put(f"{base_url}/users/{kc_id}", json=user_payload, headers=headers, timeout=5)
            print(f"DEBUG KEYCLOAK PUT USER {kc_id}: {put_resp.status_code}")
        else:
            create_resp = requests.post(f"{base_url}/users", json=user_payload, headers=headers, timeout=5)
            print(f"DEBUG KEYCLOAK CREATE USER: {create_resp.status_code} - {create_resp.text}")
            if create_resp.status_code in [200, 201, 204, 409]:
                search_resp = requests.get(f"{base_url}/users?username={username}", headers=headers, timeout=5)
                if search_resp.status_code == 200 and search_resp.json():
                    kc_id = search_resp.json()[0].get('id')

        if kc_id:
            roles_mapping_url = f"{base_url}/users/{kc_id}/role-mappings/realm"

            roles_to_assign = []
            if role_obj:
                roles_to_assign.append(role_obj)
            if additional_roles:
                roles_to_assign.extend(additional_roles)

            all_kc_roles_resp = requests.get(f"{base_url}/roles", headers=headers, timeout=5)
            print(f"DEBUG KEYCLOAK GET ROLES: {all_kc_roles_resp.status_code}")
            all_kc_roles = all_kc_roles_resp.json() if all_kc_roles_resp.status_code == 200 else []

            role_reps_to_assign = []
            for r in roles_to_assign:
                if not r or not r.name:
                    continue
                r_name = r.name
                
                role_rep = None
                for kc_role in all_kc_roles:
                    if kc_role.get('name', '').lower() == r_name.lower():
                        role_rep = kc_role
                        break

                if not role_rep:
                    create_role_resp = requests.post(f"{base_url}/roles", json={"name": r_name, "description": r.description or ""}, headers=headers, timeout=5)
                    print(f"DEBUG KEYCLOAK CREATE ROLE {r_name}: {create_role_resp.status_code}")
                    if create_role_resp.status_code in [200, 201, 204, 409]:
                        all_kc_roles_resp = requests.get(f"{base_url}/roles", headers=headers, timeout=5)
                        all_kc_roles = all_kc_roles_resp.json() if all_kc_roles_resp.status_code == 200 else []
                        for kc_role in all_kc_roles:
                            if kc_role.get('name', '').lower() == r_name.lower():
                                role_rep = kc_role
                                break

                if role_rep:
                    role_reps_to_assign.append(role_rep)

            print(f"DEBUG KEYCLOAK ROLES TO ASSIGN: {[r.get('name') for r in role_reps_to_assign]}")

            cur_roles_resp = requests.get(roles_mapping_url, headers=headers, timeout=5)
            if cur_roles_resp.status_code == 200:
                current_role_reps = cur_roles_resp.json()
                if current_role_reps:
                    del_resp = requests.delete(roles_mapping_url, json=current_role_reps, headers=headers, timeout=5)
                    print(f"DEBUG KEYCLOAK DELETE OLD ROLES: {del_resp.status_code}")

            if role_reps_to_assign:
                assign_resp = requests.post(roles_mapping_url, json=role_reps_to_assign, headers=headers, timeout=5)
                print(f"DEBUG KEYCLOAK ASSIGN ROLES RESPONSE: {assign_resp.status_code} - {assign_resp.text}")

        return kc_id
    except Exception as e:
        print(f"DEBUG KEYCLOAK SYNC EXCEPTION: {str(e)}")
        return existing_kc_id

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

        is_valid_pw, pw_msg = validate_password_complexity(password)
        if not is_valid_pw:
            ctx = {
                'error': pw_msg,
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
            role_obj = Role.objects.filter(id=int(role_id)).first() if role_id else None
            additional_role_objs = Role.objects.filter(id__in=[int(rid) for rid in additional_roles]) if additional_roles else []

            kc_id = sync_user_roles_to_keycloak(
                username=username,
                email=email,
                password=password,
                is_active=is_active,
                role_obj=role_obj,
                additional_roles=additional_role_objs,
                existing_kc_id=None
            )

            new_user = User.objects.create_user(username=username, email=email, password=password, is_active=is_active)
            new_profile = UserProfile.objects.create(user=new_user, role=role_obj, keycloak_id=kc_id)
            if additional_role_objs:
                new_profile.roles.set(additional_role_objs)

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

        if password:
            is_valid_pw, pw_msg = validate_password_complexity(password)
            if not is_valid_pw:
                ctx = {
                    'error': pw_msg,
                    'target_user': target_user,
                    'all_roles': all_roles,
                    'edit_mode': True
                }
                ctx.update(interface_ctx)
                return render(request, 'gestion_clientes/admin_user_form.html', ctx)

        try:
            role_obj = Role.objects.filter(id=int(role_id)).first() if role_id else None
            additional_role_objs = Role.objects.filter(id__in=[int(rid) for rid in additional_roles]) if additional_roles else []

            kc_id = sync_user_roles_to_keycloak(
                username=username,
                email=email,
                password=password,
                is_active=is_active,
                role_obj=role_obj,
                additional_roles=additional_role_objs,
                existing_kc_id=target_profile.keycloak_id
            )

            target_user.username = username
            target_user.email = email
            target_user.is_active = is_active
            if password:
                target_user.set_password(password)
            target_user.save()

            target_profile.role = role_obj
            target_profile.keycloak_id = kc_id or target_profile.keycloak_id
            target_profile.save()
            target_profile.roles.set(additional_role_objs)

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
