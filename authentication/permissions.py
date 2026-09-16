from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied
from .models import UsuarioClienteRelacion, UserProfile, Cliente

class TieneAccesoACliente(permissions.BasePermission):
    """
    Permission class for DRF that reads X-Cliente-Id header and validates access
    against UsuarioClienteRelacion, attaching cliente_activo to request.
    """
    def has_permission(self, request, view):
        cliente_id = request.headers.get('X-Cliente-Id') or request.META.get('HTTP_X_CLIENTE_ID') or request.GET.get('cliente_id')
        if not cliente_id:
            raise PermissionDenied("Header X-Cliente-Id es requerido.")

        profile, _ = UserProfile.objects.get_or_create(user=request.user) if request.user.is_authenticated else (None, None)
        kc_user_id = profile.keycloak_id if profile else str(request.user.id)

        if request.user.is_superuser:
            cliente = Cliente.objects.filter(id=cliente_id).first()
            if cliente:
                request.cliente_activo = cliente
                return True

        rel = UsuarioClienteRelacion.objects.filter(keycloak_user_id=kc_user_id, cliente_id=cliente_id).first()
        if rel:
            request.cliente_activo = rel.cliente
            return True

        raise PermissionDenied("No tiene acceso al cliente especificado.")
