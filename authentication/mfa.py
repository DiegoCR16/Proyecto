# -*- coding: utf-8 -*-
"""
Verificación en dos pasos (iToken) con código enviado por correo electrónico.

En cada inicio de sesión de un usuario que requiere MFA (administrativos y corporativos)
se genera un código aleatorio de 6 dígitos, se envía a su correo y se guarda en la sesión
solo como hash HMAC. El código:
  - vence a los CODIGO_VIGENCIA_SEGUNDOS,
  - se puede usar una sola vez,
  - se invalida tras MAX_INTENTOS intentos fallidos,
  - se puede reenviar cada REENVIO_ESPERA_SEGUNDOS.

Mientras la verificación está pendiente, MFAPendienteMiddleware solo permite la
pantalla del iToken y el cierre de sesión.
"""
import hashlib
import hmac
import logging
import secrets
import time

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)

CODIGO_DIGITOS = 6
CODIGO_VIGENCIA_SEGUNDOS = 5 * 60
MAX_INTENTOS = 5
REENVIO_ESPERA_SEGUNDOS = 60

SESION_PENDIENTE = 'mfa_pendiente'
SESION_HASH = 'mfa_codigo_hash'
SESION_EXPIRA = 'mfa_expira'
SESION_INTENTOS = 'mfa_intentos'
SESION_ENVIADO = 'mfa_enviado'

# Resultados de verificar_codigo()
VALIDO = 'valido'
INVALIDO = 'invalido'
EXPIRADO = 'expirado'
BLOQUEADO = 'bloqueado'
SIN_CODIGO = 'sin_codigo'


def generar_codigo():
    """Genera un código numérico aleatorio criptográficamente seguro de 6 dígitos."""
    return f'{secrets.randbelow(10 ** CODIGO_DIGITOS):0{CODIGO_DIGITOS}d}'


def _hash_codigo(codigo, user_id):
    """Hash HMAC-SHA256 del código ligado al usuario (el código nunca se guarda en claro)."""
    mensaje = f'{user_id}:{codigo}'.encode()
    return hmac.new(settings.SECRET_KEY.encode(), mensaje, hashlib.sha256).hexdigest()


def enmascarar_email(email):
    """Oculta parte del correo para mostrarlo en pantalla: diego@gmail.com -> d***o@gmail.com."""
    if not email or '@' not in email:
        return ''
    usuario, dominio = email.split('@', 1)
    if len(usuario) <= 2:
        return f'{usuario[0]}***@{dominio}'
    return f'{usuario[0]}***{usuario[-1]}@{dominio}'


def verificacion_pendiente(request):
    """Indica si la sesión actual tiene una verificación en dos pasos sin completar."""
    return bool(request.session.get(SESION_PENDIENTE))


def segundos_para_reenviar(request, ahora=None):
    """Segundos que faltan para poder pedir otro código (0 si ya se puede)."""
    ahora = time.time() if ahora is None else ahora
    enviado = request.session.get(SESION_ENVIADO)
    if not enviado:
        return 0
    return max(0, int(enviado + REENVIO_ESPERA_SEGUNDOS - ahora))


def iniciar_verificacion(request, user):
    """
    Marca la sesión como pendiente de verificación y envía un código nuevo por correo.

    Returns:
        bool: True si el correo se envió; False si el usuario no tiene correo o falló el envío.
    """
    request.session[SESION_PENDIENTE] = True
    return enviar_codigo(request, user)


def enviar_codigo(request, user, ahora=None):
    """
    Genera un código nuevo (invalida el anterior), lo guarda en la sesión y lo envía por correo.

    Returns:
        bool: True si el correo se envió correctamente.
    """
    ahora = time.time() if ahora is None else ahora
    codigo = generar_codigo()
    request.session[SESION_HASH] = _hash_codigo(codigo, user.pk)
    request.session[SESION_EXPIRA] = ahora + CODIGO_VIGENCIA_SEGUNDOS
    request.session[SESION_INTENTOS] = 0
    request.session[SESION_ENVIADO] = ahora

    if not user.email:
        logger.warning('Usuario %s sin correo: no se puede enviar el código de verificación.', user.username)
        return False
    minutos = CODIGO_VIGENCIA_SEGUNDOS // 60
    try:
        send_mail(
            subject='Global Exchange - Código de verificación',
            message=(
                f'Hola {user.get_full_name() or user.username},\n\n'
                f'Tu código de verificación (iToken) es: {codigo}\n\n'
                f'Vence en {minutos} minutos y solo puede usarse una vez.\n'
                'Si no intentaste iniciar sesión, ignora este correo y avisa al administrador.\n\n'
                'Global Exchange'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
        )
    except Exception:  # noqa: BLE001 - cualquier fallo SMTP se informa al usuario sin romper el login
        logger.exception('No se pudo enviar el código de verificación a %s.', user.username)
        return False
    return True


def verificar_codigo(request, user, codigo, ahora=None):
    """
    Valida el código ingresado contra el guardado en la sesión.

    Returns:
        str: VALIDO, INVALIDO, EXPIRADO, BLOQUEADO o SIN_CODIGO.
    """
    ahora = time.time() if ahora is None else ahora
    guardado = request.session.get(SESION_HASH)
    if not guardado:
        return SIN_CODIGO
    if ahora > request.session.get(SESION_EXPIRA, 0):
        _descartar_codigo(request)
        return EXPIRADO
    if request.session.get(SESION_INTENTOS, 0) >= MAX_INTENTOS:
        _descartar_codigo(request)
        return BLOQUEADO

    codigo = (codigo or '').strip()
    if codigo.isdigit() and hmac.compare_digest(guardado, _hash_codigo(codigo, user.pk)):
        completar_verificacion(request)
        return VALIDO

    request.session[SESION_INTENTOS] = request.session.get(SESION_INTENTOS, 0) + 1
    if request.session[SESION_INTENTOS] >= MAX_INTENTOS:
        _descartar_codigo(request)
        return BLOQUEADO
    return INVALIDO


def intentos_restantes(request):
    """Cantidad de intentos que quedan para el código actual."""
    return max(0, MAX_INTENTOS - request.session.get(SESION_INTENTOS, 0))


def completar_verificacion(request):
    """Quita la marca de pendiente y descarta el código (uso único)."""
    request.session.pop(SESION_PENDIENTE, None)
    _descartar_codigo(request)


def _descartar_codigo(request):
    """Elimina el código de la sesión (vencido, bloqueado o ya usado)."""
    for clave in (SESION_HASH, SESION_EXPIRA, SESION_INTENTOS):
        request.session.pop(clave, None)
