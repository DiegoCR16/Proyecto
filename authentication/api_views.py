from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from .models import Cliente, UsuarioClienteRelacion, UserProfile
from .serializers import ClienteSerializer
from .permissions import TieneAccesoACliente
import requests
from django.conf import settings

class ClienteListView(APIView):
    """
    Endpoint GET para listar únicamente los clientes a los que el usuario autenticado tiene acceso.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        profile, _ = UserProfile.objects.get_or_create(user=request.user) if request.user.is_authenticated else (None, None)
        kc_user_id = profile.keycloak_id if profile else str(request.user.id)

        if request.user.is_superuser:
            clientes = Cliente.objects.all()
        else:
            relaciones = UsuarioClienteRelacion.objects.filter(keycloak_user_id=kc_user_id).select_related('cliente')
            clientes = [rel.cliente for rel in relaciones]

        serializer = ClienteSerializer(clientes, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

class ClienteCreateView(APIView):
    """
    Endpoint POST para crear un nuevo Cliente (registra en PostgreSQL y crea el grupo en Keycloak con atributo ci_ruc).
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ClienteSerializer(data=request.data)
        if serializer.is_valid():
            cliente = serializer.save()

            # Crear grupo en Keycloak con atributo ci_ruc
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
                    groups_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/groups"
                    
                    requests.post(groups_url, json={"name": cliente.nombre_o_razon_social}, headers=headers, timeout=3)
                    
                    get_groups_resp = requests.get(groups_url, headers=headers, timeout=3)
                    if get_groups_resp.status_code == 200:
                        for g in get_groups_resp.json():
                            if g.get('name') == cliente.nombre_o_razon_social:
                                group_id = g.get('id')
                                update_url = f"{settings.KEYCLOAK_SERVER_URL}/admin/realms/{settings.KEYCLOAK_REALM}/groups/{group_id}"
                                requests.put(update_url, json={
                                    "name": cliente.nombre_o_razon_social,
                                    "attributes": {
                                        "ci_ruc": [cliente.documento_identidad]
                                    }
                                }, headers=headers, timeout=3)
                                break
            except Exception:
                pass

            profile, _ = UserProfile.objects.get_or_create(user=request.user) if request.user.is_authenticated else (None, None)
            kc_user_id = profile.keycloak_id if profile else str(request.user.id)
            if kc_user_id:
                UsuarioClienteRelacion.objects.get_or_create(
                    keycloak_user_id=kc_user_id,
                    cliente=cliente,
                    defaults={'rol_en_cliente': 'ADMIN'}
                )

            return Response(ClienteSerializer(cliente).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
