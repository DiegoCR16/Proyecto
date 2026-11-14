from django.db import migrations

PERMISOS = [
    ('can_manage_invoices', 'Gestionar Facturación Electrónica',
     'Reintentar, consultar, cancelar y anular facturas con nota de crédito; resolver solicitudes de anulación.'),
    ('can_request_invoice_cancellation', 'Solicitar Anulación de Factura',
     'Pedir al Administrador la anulación de la factura de una operación.'),
]
ROLES_CON_SOLICITUD = ['Cliente', 'Jefe (Corporativo)', 'Operador (Corporativo)']


def agregar_permisos(apps, schema_editor):
    """
    Crea los permisos de facturación y otorga «Solicitar Anulación de Factura» a los roles de
    cliente que el Administrador ya había configurado (los no configurados lo reciben por defecto).
    """
    Permission = apps.get_model('authentication', 'Permission')
    Role = apps.get_model('authentication', 'Role')
    creados = {}
    for codename, nombre, descripcion in PERMISOS:
        perm = Permission.objects.filter(codename=codename).first()
        if perm is None:
            if Permission.objects.filter(name=nombre).exists():
                nombre = f"{nombre} ({codename})"
            perm = Permission.objects.create(codename=codename, name=nombre, description=descripcion)
        creados[codename] = perm
    for rol in Role.objects.filter(name__in=ROLES_CON_SOLICITUD, permisos_configurados=True):
        rol.permissions.add(creados['can_request_invoice_cancellation'])


class Migration(migrations.Migration):
    """Permisos de facturación electrónica (anulación de facturas y notas de crédito)."""

    dependencies = [
        ('authentication', '0022_role_permisos_configurados'),
    ]

    operations = [
        migrations.RunPython(agregar_permisos, migrations.RunPython.noop),
    ]
