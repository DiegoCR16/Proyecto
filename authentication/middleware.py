# -*- coding: utf-8 -*-
"""Middleware que convierte los errores inesperados del administrador en notificaciones."""
import logging

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

logger = logging.getLogger(__name__)


def es_administrador(user):
    """
    Indica si el usuario autenticado es administrador del sistema.

    Args:
        user (User): Usuario de la solicitud.

    Returns:
        bool: True si es superusuario o su rol principal es de administrador.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    profile = getattr(user, "profile", None)
    role = getattr(profile, "role", None)
    return bool(role and "admin" in role.name.lower())


def describir_error(exc):
    """
    Arma un texto breve y legible del error para mostrarlo al administrador.

    Args:
        exc (Exception): Excepción ocurrida.

    Returns:
        str: Descripción con el tipo de error y su detalle.
    """
    mensajes = getattr(exc, "messages", None)  # ValidationError de Django
    detalle = "; ".join(str(m) for m in mensajes) if mensajes else str(exc)
    detalle = (detalle or "sin detalle").strip()[:300]
    return f"{type(exc).__name__}: {detalle}"


class NotificarErroresAdminMiddleware:
    """
    Si una vista falla con un error no controlado mientras la usa un administrador,
    registra el error en el log y vuelve a la pantalla anterior con una notificación
    que explica qué pasó, en lugar de mostrar la página de error 500.

    No intercepta las llamadas a la API (rutas /api/ o solicitudes que esperan JSON),
    que deben seguir respondiendo con su código de error.
    """

    def __init__(self, get_response):
        """Guarda el siguiente manejador de la cadena de middlewares."""
        self.get_response = get_response

    def __call__(self, request):
        """Procesa la solicitud sin modificarla."""
        return self.get_response(request)

    def process_exception(self, request, exc):
        """
        Convierte la excepción en una notificación para el administrador.

        Returns:
            HttpResponseRedirect | None: Redirección con la notificación, o None para
            que Django maneje el error como siempre.
        """
        if not es_administrador(getattr(request, "user", None)):
            return None
        if "/api/" in request.path or "application/json" in request.headers.get("Accept", ""):
            return None

        logger.exception("Error en la interfaz de administración (%s %s)", request.method, request.path)

        destino = reverse("dashboard_redirect")
        anterior = request.headers.get("Referer", "")
        if (
            anterior
            and url_has_allowed_host_and_scheme(anterior, allowed_hosts={request.get_host()})
            and not anterior.endswith(request.get_full_path())
        ):
            destino = anterior
        elif request.path == destino:
            # Falló el propio panel: redirigir a él mismo generaría un ciclo
            return None

        messages.error(
            request,
            f"No se pudo completar la operación en {request.path}. Detalle: {describir_error(exc)}",
        )
        return redirect(destino)
