from rest_framework import serializers
from .models import Cliente, UsuarioClienteRelacion

class ClienteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cliente
        fields = ['id', 'nombre_o_razon_social', 'tipo_cliente', 'documento_identidad', 'email', 'categoria', 'creado_en']
        read_only_fields = ['id', 'creado_en']
