from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('sso/', views.keycloak_login_redirect, name='keycloak_login'),
    path('register/', views.register_view, name='register'),
    path('register/sso/', views.keycloak_register_redirect, name='keycloak_register'),
    path('callback/', views.keycloak_callback_view, name='keycloak_callback'),
    path('mfa/', views.mfa_verify_view, name='mfa_verify'),
    path('dashboard/', views.dashboard_redirect_view, name='dashboard_redirect'),
    path('logout/', views.logout_view, name='logout'),
]
