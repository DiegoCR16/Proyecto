# Generated for SIFEN/clientes: guardar el correo indicado al solicitar ser cliente

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('authentication', '0020_auto_20261011_1729'),
    ]

    operations = [
        migrations.AddField(
            model_name='clientregistrationrequest',
            name='email',
            field=models.EmailField(blank=True, default='', max_length=254, verbose_name='Correo Electrónico del Cliente'),
        ),
    ]
