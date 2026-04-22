# AGENTS for trading-research-app

Codex debe leer este archivo antes de trabajar en este proyecto.

## VPS Context

- Workspace operativo: `/workspace`
- Contenido real: `/opt/peremarc/workspace`
- Usuario de desarrollo: `pere`
- Las apps deben correr en host dentro de sesiones `tmux`
- Docker queda para servicios auxiliares
- Traefik publica dominios `dev-*` y reenvia a `127.0.0.1`

## Read First

- `/workspace/WORKSPACE.md`
- `/opt/peremarc/workspace/.workspace/projects/trading-research-app.sh`
- `/opt/peremarc/traefik/dynamic/project-trading-research-app.yml`

## Project Init State

- Tipo solicitado: `python-fastapi`
- Puerto reservado: `15180`
- Definicion operativa: `/opt/peremarc/workspace/.workspace/projects/trading-research-app.sh`
- Directorio del proyecto: `/workspace/apps/trading-research-app`
- URL publica prevista: `https://dev-trading-research-app.peremarc.com`
- Ruta Traefik prevista: `/opt/peremarc/traefik/dynamic/project-trading-research-app.yml`

Se ha preparado un bootstrap base para una app Python/FastAPI con `.venv`, `requirements.txt` y `uvicorn app.main:app`.

## What Codex Must Do

Antes de implementar:

1. Lee `/workspace/WORKSPACE.md` y este `AGENTS.md`.
2. Inspecciona el repo actual y decide la estructura minima correcta.
3. Revisa `/opt/peremarc/workspace/.workspace/projects/trading-research-app.sh` y ajustalo al runtime real del proyecto.

Objetivo operativo:

- El proyecto debe arrancar con `project-up trading-research-app`.
- `project-check trading-research-app` debe describir bien el bind y, si aplica, el healthcheck.
- `project-logs trading-research-app` debe mostrar un arranque util y estable.
- Si el proyecto usa solo Node o Python, debe correr en host.
- Docker solo debe usarse para base de datos, Ollama, gateways, escritorios remotos u otros auxiliares claros.
- Si el proyecto necesita varios procesos, dejalos modelados explicitamente en `/opt/peremarc/workspace/.workspace/projects/trading-research-app.sh`.
- Mantener `.env.example` al dia.
- Si hay publicacion por dominio, mantener en sync la definicion operativa y la ruta Traefik.

Puntos especificos para este tipo:

- Mantener host-run por defecto.
- Si el modulo de arranque no es `app.main:app`, actualiza `project_dev_command`.
- Si necesitas migraciones, seeds o build adicional, anadelo en `project_bootstrap`.

## Workspace Integration Checklist

- Confirmar estructura de carpetas del proyecto
- Definir el comando real de desarrollo
- Ajustar `project_bootstrap` si faltan dependencias, build o setup
- Ajustar `project_dev_command` al entrypoint real
- Ajustar `HEALTH_URL` si procede
- Crear o actualizar `.env.example`
- Verificar que `project-up trading-research-app` funciona
- Verificar que `project-check trading-research-app` refleja el runtime correcto

## Useful Commands

```bash
cd /workspace/apps/trading-research-app
codex
project-up trading-research-app
project-check trading-research-app
project-logs trading-research-app 120
project-down trading-research-app
```
