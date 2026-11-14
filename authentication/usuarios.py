# -*- coding: utf-8 -*-
"""
Utilidades para mantener alineados los nombres de usuario de Django con los de Keycloak.

Keycloak guarda siempre el username en minúsculas. Si Django lo guarda con mayúsculas
("Diego"), al iniciar sesión Keycloak devuelve "diego" y se termina creando un usuario
duplicado. Por eso todos los usernames se normalizan a minúsculas y las búsquedas no
distinguen mayúsculas.
"""
from django.contrib.auth.models import User


def normalizar_username(username):
    """
    Normaliza un nombre de usuario igual que Keycloak: sin espacios alrededor y en minúsculas.

    Args:
        username (str): Nombre de usuario ingresado o recibido de Keycloak.

    Returns:
        str: Nombre de usuario normalizado ("" si viene vacío).
    """
    return (username or "").strip().lower()


def existe_username(username, excluir_id=None):
    """
    Indica si ya hay un usuario con ese nombre, sin distinguir mayúsculas.

    Args:
        username (str): Nombre de usuario a verificar.
        excluir_id (int, optional): ID de usuario a ignorar (el que se está editando).

    Returns:
        bool: True si el nombre ya está en uso.
    """
    qs = User.objects.filter(username__iexact=normalizar_username(username))
    if excluir_id is not None:
        qs = qs.exclude(id=excluir_id)
    return qs.exists()


def obtener_o_crear_usuario_keycloak(username, kc_id=None, defaults=None):
    """
    Devuelve el usuario de Django que corresponde a una cuenta de Keycloak, creándolo si no existe.

    Busca primero por el ID de Keycloak guardado en el perfil, después por username sin
    distinguir mayúsculas, y solo si no encuentra ninguno crea el usuario en minúsculas.

    Args:
        username (str): Username recibido de Keycloak.
        kc_id (str, optional): ID ("sub") del usuario en Keycloak.
        defaults (dict, optional): Campos para el usuario si hay que crearlo (email, first_name...).

    Returns:
        tuple: (User, bool) con el usuario y si fue creado.
    """
    if kc_id:
        user = User.objects.filter(profile__keycloak_id=kc_id).order_by("id").first()
        if user:
            return user, False

    username = normalizar_username(username)
    user = User.objects.filter(username__iexact=username).order_by("id").first()
    if user:
        return user, False

    return User.objects.create(username=username, **(defaults or {})), True
