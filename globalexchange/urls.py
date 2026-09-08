from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect

urlpatterns = [
    path('admin/', admin.site.urls),
    path('auth/', include('authentication.urls')),
    path('auth/', include('gestion_clientes.urls')),
    path('tasas/', include('tasas_cambio.urls')),
    path('', lambda request: redirect('auth/login/')),
]
