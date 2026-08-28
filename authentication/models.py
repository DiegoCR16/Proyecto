from django.db import models
from django.contrib.auth.models import User

class Role(models.Model):
    """
    Modelo que representa un Rol dentro del sistema Global Exchange.
    Ej: Administrador, Corporativo, Individual, Operador, Visualizador.
    """
    name = models.CharField(max_length=50, unique=True, verbose_name="Nombre del Rol")
    description = models.TextField(blank=True, null=True, verbose_name="Descripción")

    def __str__(self):
        return self.name

class UserProfile(models.Model):
    """
    Perfil extendido del usuario para almacenar información de Keycloak,
    clasificación de cliente, rol y estado de autenticación de doble factor (MFA/iToken).
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile', verbose_name="Usuario")
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Rol")
    is_corporate = models.BooleanField(default=False, verbose_name="Es Cliente Corporativo")
    mfa_enabled = models.BooleanField(default=False, verbose_name="MFA / iToken Habilitado")
    itoken_verified = models.BooleanField(default=False, verbose_name="iToken Verificado")
    keycloak_id = models.CharField(max_length=255, blank=True, null=True, unique=True, verbose_name="ID de Keycloak")

    def requires_mfa(self):
        """
        Determina si el usuario requiere obligatoriamente MFA/iToken según la regla de negocio:
        Usuarios administrativos o Clientes Corporativos.
        """
        if self.is_corporate:
            return True
        if self.role and self.role.name.lower() in ['admin', 'administrador', 'operador']:
            return True
        return self.mfa_enabled

    def __str__(self):
        return f"{self.user.username} - {self.role.name if self.role else 'Sin Rol'}"

class AuditLog(models.Model):
    """
    Registro de auditoría para intentos de inicio de sesión, fallos de seguridad y eventos del sistema.
    """
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Usuario")
    action = models.CharField(max_length=255, verbose_name="Acción / Evento")
    ip_address = models.GenericIPAddressField(blank=True, null=True, verbose_name="Dirección IP")
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="Fecha y Hora")
    details = models.TextField(blank=True, null=True, verbose_name="Detalles")

    def __str__(self):
        username = self.user.username if self.user else "Anónimo"
        return f"[{self.timestamp}] {username} - {self.action}"
