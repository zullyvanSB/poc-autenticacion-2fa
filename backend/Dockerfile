# syntax=docker/dockerfile:1.7

# =============================================================================
# Backend_FastAPI — imagen Docker unica (R9.2, R9.3)
# -----------------------------------------------------------------------------
# Una sola imagen, identica en digest, apta para ejecutarse tanto en local
# (docker run) como en Cloud Run sin reconstruccion (R9.2). Toda la
# configuracion y los secretos se obtienen EXCLUSIVAMENTE de variables de
# entorno en tiempo de ejecucion: no se hornea ningun secreto ni configuracion
# en la imagen (R9.3, steering: Secretos via env, no en imagen).
#
# La reproducibilidad del digest exige fijar la imagen base por digest (no solo
# por tag) y resolver dependencias con versiones exactas desde JFrog
# (pyproject.toml ya las fija sin ^/~/*). El build NO recibe secretos de runtime;
# unicamente credenciales de JFrog como build args efimeros (ver mas abajo),
# que no quedan en la imagen final al vivir solo en la etapa `builder`.
# =============================================================================

# -----------------------------------------------------------------------------
# Imagen base fijada por tag de parche + digest para builds reproducibles.
# El digest es lo que garantiza "identica en digest" (R9.2); el tag legible
# documenta la version de Python 3.12 LTS (steering: tech-stack Python 3.12+).
# Para actualizar la base: cambiar tag Y digest de forma conjunta.
# -----------------------------------------------------------------------------
ARG PYTHON_IMAGE=python:3.12.11-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254

# =============================================================================
# Etapa 1: builder — instala dependencias con Poetry desde JFrog.
# =============================================================================
FROM ${PYTHON_IMAGE} AS builder

# Entorno de build determinista y sin prompts.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VERSION=1.8.5 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_VIRTUALENVS_CREATE=true

# ---------------------------------------------------------------------------
# Credenciales de JFrog para resolver dependencias en tiempo de BUILD.
# Se pasan como build args efimeros (no como secretos de runtime) y solo
# existen en esta etapa `builder`; la imagen final (etapa `runtime`) NO las
# contiene. Uso:
#   docker build \
#     --build-arg JFROG_USER=<usuario> \
#     --build-arg JFROG_PASSWORD=<token-o-password> \
#     -t auth-2fa-poc-backend:local backend
# Alternativa recomendada (no deja el valor en el historial de capas con
# BuildKit): usar `--secret`. Aqui se usan build args por simplicidad de la
# PoC; el `pip`/`poetry` los consume solo durante la resolucion.
# ---------------------------------------------------------------------------
ARG JFROG_USER=""
ARG JFROG_PASSWORD=""
# URL del indice PyPI de JFrog; debe coincidir con la fuente `jfrog` del
# pyproject.toml. Se parametriza para no acoplar el host al Dockerfile.
ARG JFROG_PYPI_URL="https://jfrog.segurosbolivar.com/artifactory/api/pypi/pypi/simple/"

# Instala Poetry. Si hay credenciales de JFrog, instala Poetry desde el indice
# institucional; de lo contrario usa el indice por defecto configurado en el
# entorno de build (steering: dependencias desde JFrog).
RUN if [ -n "${JFROG_USER}" ] && [ -n "${JFROG_PASSWORD}" ]; then \
        PRE="$(printf '%s' "${JFROG_PYPI_URL}" | sed -E 's#^https://#https://'"${JFROG_USER}:${JFROG_PASSWORD}"'@#')"; \
        pip install --index-url "${PRE}" "poetry==${POETRY_VERSION}"; \
    else \
        pip install "poetry==${POETRY_VERSION}"; \
    fi

WORKDIR /app

# Copia solo los manifiestos primero para aprovechar la cache de capas: las
# dependencias solo se reinstalan si cambian pyproject.toml / poetry.lock.
COPY pyproject.toml ./
# El lock es opcional en el repo de la PoC; se copia si existe.
COPY poetry.lock* ./

# Configura las credenciales de JFrog en Poetry (fuente `jfrog` del
# pyproject.toml) e instala SOLO dependencias de runtime (sin dev, sin el
# propio paquete). Las credenciales se limpian al terminar la etapa.
RUN if [ -n "${JFROG_USER}" ] && [ -n "${JFROG_PASSWORD}" ]; then \
        poetry config http-basic.jfrog "${JFROG_USER}" "${JFROG_PASSWORD}"; \
    fi && \
    poetry install --only main --no-root --no-ansi

# =============================================================================
# Etapa 2: runtime — imagen final minima, sin herramientas de build ni
# credenciales. Ejecuta uvicorn como PID 1 para recibir SIGTERM directamente
# y hacer graceful shutdown (R: resiliencia / graceful shutdown).
# =============================================================================
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Cloud Run inyecta PORT; en local se usa el default 8080 (R9.2).
    PORT=8080 \
    # El venv del builder se expone en el PATH.
    PATH="/app/.venv/bin:${PATH}"

# Usuario no-root dedicado (steering: ejecutar como non-root). UID/GID fijos
# para permisos predecibles entre local y Cloud Run.
RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

# Copia el entorno virtual ya resuelto desde el builder (solo artefactos, sin
# credenciales ni cache de pip).
COPY --from=builder /app/.venv /app/.venv

# Copia el codigo de la aplicacion. `src/main.py` expone `app = create_app()`,
# por lo que uvicorn sirve `main:app` con el directorio `src` en el path.
COPY src ./src

# La imagen no contiene secretos ni .env: todo llega por variables de entorno
# en tiempo de ejecucion (R9.3). El arranque aborta si falta alguna requerida
# (StartupValidator, R9.4).

USER appuser

# Documenta el puerto; el valor efectivo lo fija PORT en tiempo de ejecucion.
EXPOSE 8080

# Healthcheck opcional para orquestadores locales (Cloud Run usa su propio
# probe). Consulta el endpoint de disponibilidad versionado.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; url=f\"http://127.0.0.1:{os.environ.get('PORT','8080')}/api/v1/health\"; sys.exit(0 if urllib.request.urlopen(url, timeout=2).status==200 else 1)"

# uvicorn se ejecuta como PID 1 (forma exec de CMD) para recibir SIGTERM de
# Docker / Cloud Run y cerrar de forma ordenada (graceful shutdown): deja de
# aceptar conexiones nuevas, termina las en curso y libera recursos. El puerto
# se toma de la variable PORT (requisito de Cloud Run; default 8080).
# `--app-dir src` permite importar `main:app` desde el paquete.
CMD ["sh", "-c", "exec uvicorn main:app --app-dir src --host 0.0.0.0 --port ${PORT} --timeout-graceful-shutdown 25"]
