from django.urls import path
from . import views

urlpatterns = [
    path('clients/', views.admin_client_list_view, name='admin_client_list'),
    path('clients/<int:user_id>/', views.admin_client_detail_view, name='admin_client_detail'),
]
