from django.db import migrations, models


class Migration(migrations.Migration):
    """Agrega Role.permisos_configurados para distinguir roles configurados de los que usan permisos por defecto."""

    dependencies = [
        ('authentication', '0021_clientregistrationrequest_email'),
    ]

    operations = [
        migrations.AddField(
            model_name='role',
            name='permisos_configurados',
            field=models.BooleanField(default=False, verbose_name='Permisos Configurados por el Administrador'),
        ),
    ]
