# -*- coding: utf-8 -*-
from django.urls import path
from . import views

app_name = 'caja'

urlpatterns = [
    path('gestion/', views.gestion_caja_view, name='gestion_caja'),
    path('abrir/<int:caja_id>/', views.abrir_turno_view, name='abrir_turno'),
    path('cerrar/<int:turno_id>/', views.cerrar_turno_view, name='cerrar_turno'),
]
