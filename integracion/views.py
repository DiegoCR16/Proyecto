# -*- coding: utf-8 -*-
import json
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from integracion.models import PaymentGatewayLog


@csrf_exempt
def stripe_webhook_view(request):
    """
    Endpoint Webhook Server-to-Server para procesar confirmaciones asíncronas de Stripe (`payment_intent.succeeded`).
    
    Args:
        request (HttpRequest): Petición HTTP recibida desde Stripe.
        
    Returns:
        JsonResponse: Respuesta HTTP JSON confirmando la recepción del evento.
    """
    if request.method != 'POST':
        return HttpResponseBadRequest("Método no permitido.")

    try:
        payload = request.body
        event = json.loads(payload.decode('utf-8'))
        event_type = event.get('type')

        if event_type == 'payment_intent.succeeded':
            payment_intent = event.get('data', {}).get('object', {})
            pi_id = payment_intent.get('id')
            
            log_entry = PaymentGatewayLog.objects.filter(reference_code=pi_id).first()
            if log_entry:
                log_entry.status = 'SUCCESS'
                log_entry.save()

        return JsonResponse({"status": "success", "received": True}, status=200)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)
