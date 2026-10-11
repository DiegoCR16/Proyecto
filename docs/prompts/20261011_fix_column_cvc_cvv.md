# Corrección de Error: column authentication_clientaccreditationmethod.cvc_vv does not exist

## Fecha: 11 de Octubre, 2026

## Problema
Al acceder a `/operaciones/compra/`, se produjo un error de programación en Django (`ProgrammingError`):
`column authentication_clientaccreditationmethod.cvc_vv does not exist`

## Causa
La migración de la base de datos que agregaba el campo `cvc_cvv` a la entidad `ClientAccreditationMethod` (`authentication.0019_clientaccreditationmethod_cvc_cvv`) estaba marcada como aplicada en la tabla de migraciones de Django (`django_migrations`), pero la columna física no existía en la base de datos SQLite.

## Solución Aplicada
1. Se desaplicó la migración hasta el punto previo (`0018`):
   ```bash
   python manage.py migrate authentication 0018
   ```
2. Se re-aplicaron las migraciones correspondientes de la app `authentication`:
   ```bash
   python manage.py migrate authentication
   ```
3. Se verificó el funcionamiento correcto de las vistas y tests unitarios de compra de divisas.
