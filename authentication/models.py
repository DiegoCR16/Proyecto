from django.db import models
from django.contrib.auth.models import User
from decimal import Decimal
import uuid
import re

class Permission(models.Model):
    """
    Modelo que representa un permiso granular dentro del sistema Global Exchange.
    
    Attributes:
        name (CharField): Nombre descriptivo del permiso (Ej: Crear Tasa, Editar Cliente, Gestionar Roles).
        codename (CharField): Código único del permiso (Ej: can_manage_roles, can_edit_rates).
        description (TextField): Descripción del alcance del permiso.
    """
    name = models.CharField(max_length=100, unique=True, verbose_name="Nombre del Permiso")
    codename = models.CharField(max_length=100, unique=True, verbose_name="Código del Permiso")
    description = models.TextField(blank=True, null=True, verbose_name="Descripción")

    def __str__(self):
        """Devuelve el nombre y código del permiso."""
        return f"{self.name} ({self.codename})"

class Role(models.Model):
    """
    Modelo que representa un Rol dentro del sistema Global Exchange con soporte para permisos granulares y estado activo/inactivo.
    
    Attributes:
        name (CharField): Nombre único del rol (Ej: Administrador, Corporativo, Individual).
        description (TextField): Descripción detallada del rol y sus privilegios.
        is_active (BooleanField): Indica si el rol está activo en la plataforma.
        permissions (ManyToManyField): Permisos granulares asignados al rol.
    """
    name = models.CharField(max_length=50, unique=True, verbose_name="Nombre del Rol")
    description = models.TextField(blank=True, null=True, verbose_name="Descripción")
    is_active = models.BooleanField(default=True, verbose_name="Rol Activo")
    permissions = models.ManyToManyField(Permission, blank=True, related_name='roles', verbose_name="Permisos Asignados")

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
    roles = models.ManyToManyField(Role, blank=True, related_name='user_profiles', verbose_name="Roles Adicionales")
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
        ('CLIENTE', 'Cliente'),
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

class ClientRegistrationRequest(models.Model):
    """
    Solicitud de registro de cliente (física o jurídica) pendiente de aprobación por el Administrador.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='client_requests', verbose_name="Usuario Solicitante")
    client_name = models.CharField(max_length=255, verbose_name="Nombre / Razón Social del Cliente")
    ci_ruc = models.CharField(max_length=50, unique=True, verbose_name="Cédula o RUC")
    client_type = models.CharField(max_length=20, choices=[('FISICA', 'Persona Física'), ('JURIDICA', 'Persona Jurídica')], default='FISICA', verbose_name="Tipo de Cliente")
    corporate_group = models.ForeignKey(CorporateGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name='registration_request', verbose_name="Grupo Creado")
    status = models.CharField(max_length=20, choices=[('PENDING', 'Pendiente'), ('APPROVED', 'Aprobado'), ('REJECTED', 'Rechazado')], default='PENDING', verbose_name="Estado")
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Solicitud")

    def __str__(self):
        return f"Solicitud Cliente: {self.client_name} ({self.client_type}) - {self.status}"

class MemberRequest(models.Model):
    """
    Solicitud del cliente principal para asociar a un usuario a su grupo cliente como Operador o Analista.
    """
    corporate_group = models.ForeignKey(CorporateGroup, on_delete=models.CASCADE, related_name='member_requests', verbose_name="Grupo Cliente")
    requester = models.ForeignKey(User, on_delete=models.CASCADE, related_name='requested_memberships', verbose_name="Cliente Solicitante")
    target_email = models.EmailField(verbose_name="Correo del Usuario a Asociar")
    role_requested = models.CharField(max_length=20, choices=[('OPERADOR', 'Operador'), ('ANALISTA', 'Analista')], default='OPERADOR', verbose_name="Rol Solicitado")
    status = models.CharField(max_length=20, choices=[('PENDING', 'Pendiente'), ('APPROVED', 'Aprobado'), ('REJECTED', 'Rechazado')], default='PENDING', verbose_name="Estado")
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Solicitud")

    def __str__(self):
        return f"Solicitud Miembro ({self.role_requested}): {self.target_email} -> {self.corporate_group.group_name} ({self.status})"

class Cliente(models.Model):
    """
    Modelo que representa un Cliente (Persona Física o Jurídica) almacenado en la base de datos relacional (PostgreSQL).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nombre_o_razon_social = models.CharField(max_length=255, verbose_name="Nombre o Razón Social")
    tipo_cliente = models.CharField(max_length=20, choices=[('FISICA', 'Persona Física'), ('FISICO', 'Persona Física'), ('JURIDICA', 'Persona Jurídica'), ('JURIDICO', 'Persona Jurídica')], default='FISICA', verbose_name="Tipo de Cliente")
    documento_identidad = models.CharField(max_length=50, unique=True, db_index=True, verbose_name="Cédula o RUC")
    email = models.EmailField(verbose_name="Correo Electrónico")
    categoria = models.CharField(max_length=50, default='MINORISTA', verbose_name="Categoría")
    transaction_volume = models.DecimalField(max_digits=15, decimal_places=2, default=0.00, verbose_name="Volumen Transaccional (Gs)")
    creado_en = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Creación")

    def __str__(self):
        return f"{self.nombre_o_razon_social} ({self.documento_identidad}) - {self.tipo_cliente}"

class UsuarioClienteRelacion(models.Model):
    """
    Modelo para mapear qué usuarios operan en qué cliente y con qué rol.
    """
    keycloak_user_id = models.CharField(max_length=255, db_index=True, verbose_name="Keycloak User ID")
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='usuarios_relacionados', verbose_name="Cliente")
    rol_en_cliente = models.CharField(max_length=50, default='ADMIN', verbose_name="Rol en el Cliente")

    class Meta:
        unique_together = ('keycloak_user_id', 'cliente')
        verbose_name = "Relación Usuario-Cliente"
        verbose_name_plural = "Relaciones Usuario-Cliente"

    def __str__(self):
        return f"User {self.keycloak_user_id} -> {self.cliente.nombre_o_razon_social} ({self.rol_en_cliente})"


class ClientAccreditationMethod(models.Model):
    """
    Modelo que representa un medio de acreditación de fondos del cliente (cuentas bancarias, alias de transferencias y billeteras electrónicas)
    con sus campos específicos (número de cuenta, tipo de cuenta, teléfono, alias, entidad y titularidad) (PSE-33).
    
    Attributes:
        cliente (ForeignKey): Cliente al que pertenece el medio de acreditación.
        user (ForeignKey): Usuario que registró el medio.
        tipo_medio (CharField): Tipo de medio ('CUENTA_BANCARIA', 'BILLETERA', 'ALIAS').
        entidad_financiera (CharField): Entidad financiera o proveedora (Banco, Cooperativa, Billetera).
        numero_cuenta (CharField): Número de cuenta bancaria.
        tipo_cuenta (CharField): Tipo de cuenta ('CORRIENTE', 'AHORRO', 'OTRO').
        numero_telefono (CharField): Número de teléfono para billetera electrónica.
        alias_transferencia (CharField): Alias de transferencia bancaria o billetera.
        titularidad (CharField): Titular de la cuenta o medio.
        estado (CharField): Estado de verificación ('VERIFICADO', 'PENDIENTE').
        es_predeterminado (BooleanField): Indica si es el medio predeterminado para operaciones.
        creado_en (DateTimeField): Fecha y hora de creación.
        actualizado_en (DateTimeField): Fecha y hora de última actualización.
    """
    TIPO_MEDIO_CHOICES = [
        ('CUENTA_BANCARIA', 'Cuenta Bancaria'),
        ('BILLETERA', 'Billetera Electrónica'),
        ('ALIAS', 'Alias de Transferencia'),
    ]
    TIPO_CUENTA_CHOICES = [
        ('CORRIENTE', 'Cuenta Corriente'),
        ('AHORRO', 'Caja de Ahorro'),
        ('OTRO', 'Otro / General'),
    ]
    ESTADO_CHOICES = [
        ('VERIFICADO', 'Verificado'),
        ('PENDIENTE', 'Pendiente'),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='acreditation_methods', verbose_name="Cliente")
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Usuario Registrador")
    tipo_medio = models.CharField(max_length=30, choices=TIPO_MEDIO_CHOICES, default='CUENTA_BANCARIA', verbose_name="Tipo de Medio")
    entidad_financiera = models.CharField(max_length=150, verbose_name="Banco / Entidad Financiera / Proveedor")
    
    # Campos específicos según tipo
    numero_cuenta = models.CharField(max_length=100, blank=True, null=True, verbose_name="Número de Cuenta")
    tipo_cuenta = models.CharField(max_length=30, choices=TIPO_CUENTA_CHOICES, blank=True, null=True, verbose_name="Tipo de Cuenta")
    numero_telefono = models.CharField(max_length=50, blank=True, null=True, verbose_name="Número de Teléfono / Cuenta Billetera")
    alias_transferencia = models.CharField(max_length=150, blank=True, null=True, verbose_name="Alias de Transferencia")
    
    titularidad = models.CharField(max_length=200, verbose_name="Titularidad")
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='VERIFICADO', verbose_name="Estado")
    es_predeterminado = models.BooleanField(default=False, verbose_name="Predeterminado")
    creado_en = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Creación")
    actualizado_en = models.DateTimeField(auto_now=True, verbose_name="Última Actualización")

    class Meta:
        verbose_name = "Medio de Acreditación"
        verbose_name_plural = "Medios de Acreditación"
        ordering = ['-es_predeterminado', '-creado_en']

    def __str__(self):
        """Devuelve la representación en cadena del medio de acreditación."""
        if self.tipo_medio == 'CUENTA_BANCARIA':
            detail = f"Cuenta N°: {self.numero_cuenta} ({self.get_tipo_cuenta_display()})"
        elif self.tipo_medio == 'BILLETERA':
            detail = f"Teléfono/Billetera: {self.numero_telefono}"
        else:
            detail = f"Alias: {self.alias_transferencia}"
        return f"{self.get_tipo_medio_display()} - {self.entidad_financiera} | {detail} [{self.estado}]"

    def clean(self):
        """Valida formato específico por tipo de medio y titularidad."""
        from django.core.exceptions import ValidationError
        if not self.entidad_financiera or not self.titularidad:
            raise ValidationError("La entidad financiera y la titularidad son obligatorias.")
        
        if self.tipo_medio == 'CUENTA_BANCARIA':
            if not self.numero_cuenta or not any(char.isdigit() for char in self.numero_cuenta):
                raise ValidationError("Debe especificar un número de cuenta bancaria válido con dígitos.")
        elif self.tipo_medio == 'BILLETERA':
            if not self.numero_telefono or not re.search(r'[\d\+\-\s]{7,}', self.numero_telefono):
                raise ValidationError("Debe especificar un número de teléfono o cuenta de billetera válido.")
        elif self.tipo_medio == 'ALIAS':
            if not self.alias_transferencia or len(self.alias_transferencia.strip()) < 3:
                raise ValidationError("Debe especificar un alias de transferencia válido de al menos 3 caracteres.")

    def has_pending_transactions(self):
        """
        Verifica si el medio de acreditación está asociado a alguna transacción en proceso (PENDING).
        
        Returns:
            bool: True si tiene transacciones pendientes, False en caso contrario.
        """
        from procesamiento_operaciones.models import CurrencySaleTransaction
        return CurrencySaleTransaction.objects.filter(acreditation_method=self, status='PENDING').exists()

    def save(self, *args, **kwargs):
        """Asegura validación y exclusividad de predeterminado."""
        self.full_clean()
        if self.es_predeterminado:
            ClientAccreditationMethod.objects.filter(cliente=self.cliente).exclude(pk=self.pk).update(es_predeterminado=False)
        super().save(*args, **kwargs)



