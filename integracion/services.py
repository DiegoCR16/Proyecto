# -*- coding: utf-8 -*-
import uuid
import time
import json
from decimal import Decimal
from django.core.exceptions import ValidationError
from authentication.models import Cliente, ClientAccreditationMethod
from integracion.models import PaymentGatewayLog, WithdrawalRequest
from integracion.gateways import PaymentGatewayFactory


class PaymentGatewaySimulator:
    """
    Simulador y pasarela Server-to-Server unificada (Stripe, Bancard, Tigo Money, SIPAP) para PSE-18.
    """

    @staticmethod
    def process_gateway_payment(gateway_name: str, amount: Decimal, currency: str, **kwargs) -> dict:
        """
        Procesa un pago utilizando la pasarela especificada a través del Strategy Pattern / Factory.
        
        Args:
            gateway_name (str): Nombre de la pasarela (STRIPE, SIPAP, BANCARD, TIGO_MONEY).
            amount (Decimal): Monto.
            currency (str): Moneda.
            **kwargs: Parámetros adicionales (card_number, phone_number, source_account, destination_account).
            
        Returns:
            dict: Resultado de la transacción de pasarela.
        """
        gateway = PaymentGatewayFactory.get_gateway(gateway_name)
        result = gateway.process_payment(amount, currency, **kwargs)
        
        status_db = 'SUCCESS' if result.get('status') in ['SUCCESS', 'APPROVED'] else 'FAILED'
        
        PaymentGatewayLog.objects.create(
            gateway_name=gateway_name.upper() if gateway_name.upper() in ['BANCARD', 'TIGO_MONEY', 'SIPAP'] else 'STRIPE',
            transaction_type='PAYMENT',
            reference_code=result.get('reference_code', f"REF-{uuid.uuid4().hex[:8]}"),
            amount=amount,
            currency=currency,
            status=status_db,
            request_payload=json.dumps(kwargs),
            response_payload=json.dumps(result),
            error_message=result.get('message') if status_db == 'FAILED' else ''
        )
        return result

    @staticmethod
    def simulate_bancard_payment(amount: Decimal, currency: str, card_number: str) -> dict:
        """Wrapper retrocompatible para Bancard."""
        return PaymentGatewaySimulator.process_gateway_payment('BANCARD', amount, currency, card_number=card_number)

    @staticmethod
    def simulate_tigo_money_transfer(amount: Decimal, currency: str, phone_number: str) -> dict:
        """Wrapper retrocompatible para Tigo Money."""
        return PaymentGatewaySimulator.process_gateway_payment('TIGO_MONEY', amount, currency, phone_number=phone_number)

    @staticmethod
    def simulate_sipap_transfer(amount: Decimal, currency: str, source_account: str, destination_account: str) -> dict:
        """Wrapper retrocompatible para SIPAP."""
        return PaymentGatewaySimulator.process_gateway_payment('SIPAP', amount, currency, source_account=source_account, destination_account=destination_account)


class AutomaticCreditorService:
    """
    Servicio de acreditación automática 24/7 en cuentas y métodos de acreditación del cliente (PSE-18)
    integrado directamente con el backend de compra y venta de divisas.
    """

    @staticmethod
    def credit_client_account(client_acc_id: int, amount: Decimal, currency: str, channel: str, details: str = "") -> WithdrawalRequest:
        """
        Acredita automáticamente fondos en la cuenta o medio de acreditación del cliente y registra la solicitud.
        
        Args:
            client_acc_id (int): ID de ClientAccreditationMethod.
            amount (Decimal): Monto a acreditar.
            currency (str): Moneda de la acreditación.
            channel (str): Canal utilizado (SIPAP, BANCARD, TIGO_MONEY, STRIPE, etc.).
            details (str): Detalles adicionales.
            
        Returns:
            WithdrawalRequest: Instancia de solicitud de retiro/acreditación creada y procesada.
        """
        try:
            acc = ClientAccreditationMethod.objects.get(pk=client_acc_id)
            cliente = acc.cliente
        except ClientAccreditationMethod.DoesNotExist:
            raise ValidationError("El medio de acreditación especificado no existe.")

        # Ejecutar pago/acreditación Server-to-Server vía pasarela
        gateway_resp = PaymentGatewaySimulator.process_gateway_payment(
            gateway_name=channel,
            amount=amount,
            currency=currency,
            card_number=acc.numero_cuenta or "4152310000001234",
            phone_number=acc.numero_telefono or "0981123456",
            destination_account=acc.numero_cuenta or "ACC-999"
        )

        is_success = gateway_resp.get("status") in ["SUCCESS", "APPROVED"]
        status_req = "COMPLETED" if is_success else "FAILED"

        if is_success:
            acc.balance = (acc.balance or Decimal('0.00')) + amount
            acc.save()

        withdrawal_req = WithdrawalRequest.objects.create(
            cliente=cliente,
            channel=channel.upper(),
            currency=currency,
            amount=amount,
            destination_detail=f"{acc.entidad_financiera} - {acc.numero_cuenta or acc.numero_telefono} ({details})",
            status=status_req,
            gateway_ref=gateway_resp.get("reference_code", "")
        )
        return withdrawal_req
