# -*- coding: utf-8 -*-
from django.urls import path
from integracion.views import stripe_webhook_view

app_name = 'integracion'

urlpatterns = [
    path('webhook/stripe/', stripe_webhook_view, name='stripe_webhook'),
]
