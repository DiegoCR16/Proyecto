from django.db import models
from django.contrib.auth.models import User
from decimal import Decimal

class Role(models.Model):
    """
    Modelo que representa un Rol dentro del sistema Global Exchange.
    
    Attributes:
        name (CharField): Nombre único del rol (Ej: Administrador, Corporativo, Individual).
        description (TextField): Descripción detallada del rol y sus privilegios.
    """
    name = models.CharField(max_length=50, unique=True, verbose_name="Nombre del Rol")
    description = models.TextField(blank=True, null=True, verbose_name="Descripción")

    def __str__(self):
        """Devuelve el nombre del rol como representación en cadena."""
        return self.name

class UserProfile(models.Model):
    """
    Perfil extendido del usuario para almacenar información de Keycloak,
    clasificación de cliente, rol, cédula/RUC y estado de autenticación de doble factor (MFA/iToken).
    
    Attributes:
        user (OneToOneField): Relación uno a uno con el modelo User de Django.
        role (ForeignKey): Rol asignado al usuario.
        is_corporate (BooleanField): Indicador si el usuario es cliente corporativo.
        mfa_enabled (BooleanField): Indicador si MFA/iToken está habilitado.
        itoken_verified (BooleanField): Indicador si el iToken ha sido verificado en la sesión.
        keycloak_id (CharField): Identificador único del usuario en Keycloak.
        ci_ruc (CharField): Número de cédula de identidad o RUC del cliente.
        category (CharField): Categoría de segmentación del cliente (Minorista, Corporativo, VIP).
        transaction_volume (DecimalField): Volumen transaccional acumulado en guaraníes (Gs).
    """
    CATEGORY_CHOICES = [
        ('MINORISTA', 'Minorista'),
        ('CORPORATIVO', 'Corporativo'),
        ('VIP', 'VIP'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile', verbose_name="Usuario")
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Rol")
    is_corporate = models.BooleanField(default=False, verbose_name="Es Cliente Corporativo")
    mfa_enabled = models.BooleanField(default=False, verbose_name="MFA / iToken Habilitado")
    itoken_verified = models.BooleanField(default=False, verbose_name="iToken Verificado")
    keycloak_id = models.CharField(max_length=255, blank=True, null=True, unique=True, verbose_name="ID de Keycloak")
    ci_ruc = models.CharField(max_length=20, blank=True, null=True, unique=True, verbose_name="Cédula o RUC")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='MINORISTA', verbose_name="Categoría de Cliente")
    transaction_volume = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Volumen Transaccional (Gs)")

    def requires_mfa(self):
        """
        Determina si el usuario requiere obligatoriamente MFA/iToken según la regla de negocio:
        Usuarios administrativos o Clientes Corporativos.
        
        Returns:
            bool: True si requiere MFA, False en caso contrario.
        """
        if self.is_corporate:
            return True
        if self.role and self.role.name.lower() in ['admin', 'administrador', 'operador']:
            return True
        return self.mfa_enabled

    def clean_category_assignment(self, new_category, volume=None):
        """
        Valida que la asignación de categorías según el volumen transaccional en guaraníes
        guarde coherencia con la naturaleza del cliente (Física o Jurídica).
        
        Args:
            new_category (str): Nueva categoría a asignar ('MINORISTA', 'CORPORATIVO', 'VIP').
            volume (Decimal, optional): Volumen transaccional en guaraníes a evaluar.
            
        Raises:
            ValueError: Si la asignación viola las reglas de coherencia de naturaleza o volumen.
            
        Returns:
            bool: True si la validación es exitosa.
        """
        vol = volume if volume is not None else self.transaction_volume
        if new_category == 'CORPORATIVO' and not self.is_corporate:
            raise ValueError("Los clientes de naturaleza Física (no corporativos) no pueden ser clasificados como Corporativo.")
        if new_category == 'VIP' and vol < 50000000:
            raise ValueError("Para la categoría VIP se requiere un volumen transaccional mínimo de 50.000.000 Gs.")
        if new_category == 'MINORISTA' and self.is_corporate:
            raise ValueError("Los clientes de naturaleza Jurídica no pueden tener categoría Minorista.")
        return True

    def has_active_client_association(self):
        """
        Verifica si el perfil está asociado a al menos un cliente activo en Keycloak (con keycloak_id válido y usuario activo).
        
        Returns:
            bool: True si está asociado a Keycloak y activo, False en caso contrario.
        """
        if not self.user.is_active:
            return False
        if self.user.is_superuser or (self.role and 'admin' in self.role.name.lower()):
            return True
        if self.keycloak_id:
            return True
        return False

    def perform_transaction(self, amount):
        """
        Realiza una transacción validando el bloqueo operativo si no hay asociación activa con Keycloak/cliente.
        
        Args:
            amount (Decimal or float): Monto de la transacción en guaraníes.
            
        Raises:
            PermissionError: Si el usuario no está asociado a ningún cliente activo.
            
        Returns:
            bool: True si la transacción es exitosa.
        """
        if not self.has_active_client_association():
            raise PermissionError("Bloqueo operativo: El usuario no está asociado a ningún cliente activo en Keycloak.")
        current_vol = self.transaction_volume if isinstance(self.transaction_volume, Decimal) else Decimal(str(self.transaction_volume))
        trans_amount = amount if isinstance(amount, Decimal) else Decimal(str(amount))
        self.transaction_volume = current_vol + trans_amount
        self.save()
        return True

    def __str__(self):
        """Devuelve una representación descriptiva del perfil de usuario."""
        return f"{self.user.username} - {self.role.name if self.role else 'Sin Rol'}"

class AuditLog(models.Model):
    """
    Registro de auditoría para intentos de inicio de sesión, fallos de seguridad y eventos del sistema.
    
    Attributes:
        user (ForeignKey): Usuario relacionado con el evento (si está autenticado).
        action (CharField): Acción o evento registrado.
        ip_address (GenericIPAddressField): Dirección IP desde donde se originó la petición.
        timestamp (DateTimeField): Fecha y hora exacta del evento.
        details (TextField): Detalles adicionales del evento o error.
    """
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Usuario")
    action = models.CharField(max_length=255, verbose_name="Acción / Evento")
    ip_address = models.GenericIPAddressField(blank=True, null=True, verbose_name="Dirección IP")
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="Fecha y Hora")
    details = models.TextField(blank=True, null=True, verbose_name="Detalles")

    def __str__(self):
        """Devuelve una representación formateada del registro de auditoría."""
        username = self.user.username if self.user else "Anónimo"
        return f"[{self.timestamp}] {username} - {self.action}"

class CorporateGroup(models.Model):
    """
    Representa el grupo corporativo en Keycloak asociado a una Persona Jurídica.
    
    Attributes:
        juridica_profile (ForeignKey): Perfil de la persona jurídica dueña del grupo.
        group_name (CharField): Nombre del grupo (coincide con el nombre de la persona jurídica).
        keycloak_group_id (CharField): ID único del grupo en Keycloak.
    """
    juridica_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='corporate_group', verbose_name="Perfil Persona Jurídica")
    group_name = models.CharField(max_length=255, unique=True, verbose_name="Nombre del Grupo Corporate")
    keycloak_group_id = models.CharField(max_length=255, blank=True, null=True, unique=True, verbose_name="ID de Grupo en Keycloak")

    def __str__(self):
        return f"Grupo Corporativo: {self.group_name}"

class GroupMembership(models.Model):
    """
    Representa la vinculación de una Persona Física a un Grupo Corporativo con un rol (Operador o Analista).
    
    Attributes:
        corporate_group (ForeignKey): Grupo corporativo al que pertenece.
        fisica_profile (ForeignKey): Perfil de la persona física vinculada.
        role_in_group (CharField): Rol asignado en el grupo ('OPERADOR', 'ANALISTA', 'MIEMBRO').
    """
    ROLE_CHOICES = [
        ('OPERADOR', 'Operador'),
        ('ANALISTA', 'Analista'),
        ('MIEMBRO', 'Miembro'),
    ]

    corporate_group = models.ForeignKey(CorporateGroup, on_delete=models.CASCADE, related_name='memberships', verbose_name="Grupo Corporativo")
    fisica_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name='group_memberships', verbose_name="Perfil Persona Física")
    role_in_group = models.CharField(max_length=20, choices=ROLE_CHOICES, default='OPERADOR', verbose_name="Rol en el Grupo")

    class Meta:
        unique_together = ('corporate_group', 'fisica_profile')

    def __str__(self):
        return f"{self.fisica_profile.user.username} -> {self.corporate_group.group_name} ({self.role_in_group})"

