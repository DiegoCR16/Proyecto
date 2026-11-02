from django.db import migrations, models


def reiniciar_rango(apps, schema_editor):
    """Deja la numeracion lista para que el proximo documento sea el 0000137."""
    SifenSecuencia = apps.get_model('procesamiento_operaciones', 'SifenSecuencia')
    SifenSecuencia.objects.filter(clave='001-003-1').update(ultimo_numero=136)


class Migration(migrations.Migration):

    dependencies = [
        ('procesamiento_operaciones', '0010_sifensecuencia'),
    ]

    operations = [
        migrations.AddField(
            model_name='sifensecuencia',
            name='numero_en_proceso',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='sifensecuencia',
            name='cdc_en_proceso',
            field=models.CharField(blank=True, default='', max_length=44),
        ),
        migrations.AddField(
            model_name='sifensecuencia',
            name='reservado_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(reiniciar_rango, migrations.RunPython.noop),
    ]
