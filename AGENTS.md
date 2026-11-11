# AGENTS.md - Reglas del Proyecto Global Exchange (IS2 - FPUNA)

## Contexto del Proyecto
- **Sistema:** Global Exchange (Casa de Cambios: compra/venta de divisas).
- **Asignatura:** Ingeniería de Software 2 - FPUNA.
- **Stack:** Backend Django, Keycloak (OIDC/JWT), Base de Datos Relacional, Git Flow.

## Reglas de Control de Versiones (Git Flow Mandatorio)
1. **Ramas de funcionalidad:** Crear ramas `feature/SCRUM-<ID>` desde `develop`.
   - Ejemplo: `feature/SCRUM-1234` (donde `SCRUM-1234` es el ID de la historia en Jira).
2. **Releases y Tags:** Al finalizar cada Sprint, crear un Tag de versión en la rama principal (ej. `v1.0.0`).
3. **Commits:** Cada commit debe incluir la clave del ticket (ej: `[SCRUM-1234] Implementar login con Keycloak`).

## Requisitos Académicos y Calidad
1. **Pruebas Unitarias:** Todo código nuevo debe incluir sus pruebas unitarias en Pyunit (`unittest` / `pytest`).
2. **Documentación de Código:** Escribir docstrings en funciones y clases para generación automática de docs.
3. **Registro de Conversaciones IA (CHIA - Requisito Obligatorio):**
   - Guardar las conversaciones clave con OpenCode en `docs/prompts/` en formato `.md` (ejemplo: `docs/prompts/sprint1_auth.md`).

## Guías de Requerimientos
- Los alcances detallados de cada Sprint se encuentran en la carpeta `.opencode/skills/`.
