from django.urls import path
from . import views

urlpatterns = [
    path('register/', views.register_view, name='register'),
    path('request-member/', views.request_member_view, name='request_member'),
    path('switch-group/<str:group_id>/', views.switch_group_view, name='switch_group'),
    path('switch-role/<int:role_id>/', views.switch_role_view, name='switch_role'),
    path('admin/approve-client/<int:request_id>/', views.admin_approve_client_request, name='admin_approve_client'),
    path('admin/approve-member/<int:request_id>/', views.admin_approve_member_request, name='admin_approve_member'),
    path('admin/clients/', views.admin_client_list_view, name='admin_client_list'),
    path('admin/clients/<int:user_id>/', views.admin_client_detail_view, name='admin_client_detail'),
    path('admin/users/', views.admin_user_list_view, name='admin_user_list'),
    path('admin/users/create/', views.admin_user_create_view, name='admin_user_create'),
    path('admin/users/<int:user_id>/edit/', views.admin_user_edit_view, name='admin_user_edit'),
    path('admin/users/<int:user_id>/delete/', views.admin_user_delete_view, name='admin_user_delete'),
]
