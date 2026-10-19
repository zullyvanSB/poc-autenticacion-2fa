"""Punto de entrada del Backend_FastAPI de la PoC de autenticacion y 2FA.

Esta app base monta un router versionado bajo `/api/v1/` y expone la
especificacion OpenAPI como fuente de verdad del contrato (regla de
arquitectura: API First). La logica de los endpoints (enrutamiento, sesion,
dashboard, logout) se implementa en tareas posteriores dentro de cada dominio.
"""

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from auth.router import router as auth_router
from common.api import API_V1_PREFIX, CORRELATION_ID_HEADER
from common.config import (
    StartupValidator,
    get_cors_allowed_origins,
    get_rate_limit_max_requests,
    get_rate_limit_window_seconds,
)
from common.correlation import CorrelationIdMiddleware
from common.errors import register_error_handlers
from common.logging import configure_logging
from common.rate_limit import InMemoryRateLimiter
from dashboard.router import router as dashboard_router


def create_app() -> FastAPI:
    """Construye y configura la instancia de FastAPI.

    Antes de montar nada valida la configuracion por variables de entorno: si
    falta alguna variable requerida, aborta el arranque indicando los nombres
    faltantes sin exponer valores (R9.4). Habilita explicitamente la generacion
    de OpenAPI (contrato como fuente de verdad) y monta el router versionado
    `/api/v1`.

    :raises MissingEnvironmentVariablesError: si falta configuracion requerida.
    :return: la aplicacion FastAPI configurada.
    """
    StartupValidator().validate()

    # Logger estructurado hacia stdout; necesario para que el detalle interno
    # de los errores (R10.4) y el resto de eventos se emitan con el formato y
    # el enmascarado de PII configurados.
    configure_logging()

    app = FastAPI(
        title="Backend_FastAPI - PoC Autenticacion 2FA",
        description=(
            "Servicio de autenticacion identifier-first con 2FA (TOTP). "
            "La especificacion OpenAPI es la fuente de verdad del contrato."
        ),
        version="0.1.0",
        # OpenAPI explicito como fuente de verdad del contrato.
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url="/api/v1/redoc",
    )

    # Refuerzo de rate limiting en memoria para `/auth/route` y `/auth/session`
    # (R3.7, R4.10). Estado efimero por instancia; en Cloud Run multi-instancia
    # el contador no se comparte (ver la nota de Memorystore en rate_limit.py y
    # la tarea opcional 15.3). Se expone en `app.state` para que los handlers de
    # auth lo consuman como refuerzo de los bloqueos primarios de Firebase y F5.
    app.state.rate_limiter = InMemoryRateLimiter(
        max_requests=get_rate_limit_max_requests(),
        window_seconds=get_rate_limit_window_seconds(),
    )

    # CORS configurable por entorno (APP_ENV / CORS_ALLOWED_ORIGINS). Solo se
    # registra el middleware cuando hay origenes permitidos: en produccion la
    # lista por defecto es vacia (sin CORS, fail-safe), mientras que en local se
    # habilitan los origenes del dev server de Angular. Se permiten credenciales
    # (cookies de sesion) y se expone el header de Correlation-ID para que el
    # frontend pueda leerlo en respuestas cross-origin (R10.1).
    cors_allowed_origins = get_cors_allowed_origins()
    if cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=[CORRELATION_ID_HEADER],
        )

    # Middleware transversal: garantiza un Correlation-ID por solicitud (R10.1)
    # disponible para logs (tarea 2.3) y propagacion saliente (tarea 5.3).
    app.add_middleware(CorrelationIdMiddleware)

    # Manejadores globales de error: excepcion no controlada -> 500 generico
    # (R10.4) y error de validacion de entrada -> 422 generico sin filtrar la
    # regla incumplida (R10.7). El detalle se registra solo internamente.
    register_error_handlers(app)

    api_v1_router = APIRouter(prefix=API_V1_PREFIX)

    @api_v1_router.get("/health", tags=["health"], summary="Verificacion de disponibilidad")
    def health() -> dict[str, str]:
        """Reporta que el servicio esta en ejecucion.

        :return: estado del servicio.
        """
        return {"status": "ok"}

    # Dominio de autenticacion: enrutamiento identifier-first bajo /api/v1/auth.
    # El `auth_router` ya declara su prefijo `/auth`, de modo que la ruta final
    # queda en `/api/v1/auth/route` (R2, API versioning).
    api_v1_router.include_router(auth_router)

    # Dominio del recurso protegido: dashboard bajo /api/v1/dashboard. El
    # `dashboard_router` declara su prefijo `/dashboard`, de modo que la ruta
    # final queda en `/api/v1/dashboard` (R6, API versioning).
    api_v1_router.include_router(dashboard_router)

    app.include_router(api_v1_router)

    return app


app = create_app()
