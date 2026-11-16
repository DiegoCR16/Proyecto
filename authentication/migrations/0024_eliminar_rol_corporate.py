# -*- coding: utf-8 -*-
"""
Elimina el rol "Corporate" del modelo corporativo anterior (PSE-40).

"Corporativo" es hoy una categoría de beneficio del cliente (como VIP), no un rol. Los perfiles que
todavía tuvieran ese rol pasan a "Cliente" para no quedar sin acceso. Los roles de grupo
"Jefe / Operador / Analista (Corporativo)" no se tocan: siguen en uso.
"""
from django.db import migrations


def eliminar_rol_corporate(apps, schema_editor):
    """Pasa los perfiles con rol "Corporate" a "Cliente" y borra el rol."""
    Role = apps.get_model('authentication', 'Role')
    UserProfile = apps.get_model('authentication', 'UserProfile')
    corporate = Role.objects.filter(name='Corporate').first()
    if corporate is None:
        return
    perfiles = UserProfile.objects.filter(role=corporate)
    if perfiles.exists():
        cliente, _ = Role.objects.get_or_create(
            name='Cliente', defaults={'description': 'Cliente de la Plataforma (Física y Jurídica General)'},
        )
        perfiles.update(role=cliente)
    corporate.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('authentication', '0023_permisos_facturacion'),
    ]

    operations = [
        migrations.RunPython(eliminar_rol_corporate, migrations.RunPython.noop),
    ]
