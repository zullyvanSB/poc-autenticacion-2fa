"""Contexto compartido del Correlation-ID del Backend_FastAPI.

Define un unico `ContextVar` que transporta el Correlation-ID (UUID v4) durante
todo el procesamiento de una solicitud. Tanto el `CorrelationIdMiddleware`
(que lo genera o lo lee del header entrante) como el logger estructurado y el
cliente HTTP saliente leen el valor desde este modulo, garantizando una unica
fuente de verdad para la trazabilidad (R10.1, R10.2, R10.3).

El uso de `ContextVar` asegura aislamiento por solicitud en entornos asincronos:
cada tarea de `asyncio` observa su propio valor sin interferencia entre
solicitudes concurrentes.
"""

import uuid
from contextvars import ContextVar, Token

# Valor por defecto cuando aun no se ha asociado un Correlation-ID a la solicitud
# (p. ej. logs de arranque previos a cualquier request).
_UNSET_CORRELATION_ID = "-"

# ContextVar que mantiene el Correlation-ID vigente para la solicitud en curso.
# Es la unica fuente de verdad: el middleware lo establece y el resto de la
# aplicacion (logger, cliente saliente) lo lee.
_correlation_id_ctx: ContextVar[str] = ContextVar(
    "correlation_id", default=_UNSET_CORRELATION_ID
)


def get_correlation_id() -> str:
    """Devuelve el Correlation-ID asociado a la solicitud en curso.

    Si ninguna solicitud ha establecido un valor (por ejemplo, durante el
    arranque de la aplicacion), retorna un marcador neutro en lugar de fallar.

    :return: el Correlation-ID vigente, o un marcador si no hay ninguno.
    """
    return _correlation_id_ctx.get()


def set_correlation_id(correlation_id: str) -> Token[str]:
    """Establece el Correlation-ID para la solicitud en curso.

    Lo invoca el `CorrelationIdMiddleware` tras leer el header entrante o
    generar un UUID v4 nuevo. El `Token` retornado permite restaurar el valor
    anterior con :func:`reset_correlation_id` al finalizar la solicitud.

    :param correlation_id: identificador a asociar al contexto actual.
    :return: token para restaurar el valor previo del contexto.
    """
    return _correlation_id_ctx.set(correlation_id)


def reset_correlation_id(token: Token[str]) -> None:
    """Restaura el Correlation-ID previo usando el token de :func:`set_correlation_id`.

    :param token: token devuelto al establecer el Correlation-ID.
    """
    _correlation_id_ctx.reset(token)


def generate_correlation_id() -> str:
    """Genera un nuevo Correlation-ID con formato UUID v4 (R10.1).

    :return: una cadena UUID v4 recien generada.
    """
    return str(uuid.uuid4())


from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from common.api import CORRELATION_ID_HEADER


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Middleware que garantiza un Correlation-ID por solicitud (R10.1).

    Lee el header ``X-Correlation-ID`` entrante; si falta o viene vacio tras
    recortar espacios, genera un UUID v4 (R10.1). El valor se asocia al
    `ContextVar` compartido durante todo el procesamiento, de modo que el logger
    estructurado y el cliente HTTP saliente puedan incluirlo y propagarlo
    (R10.2, R10.3), y se refleja en el header de la respuesta para que el cliente
    pueda correlacionar.

    El contexto se restablece al finalizar cada solicitud para evitar que un
    Correlation-ID se filtre entre solicitudes concurrentes que reutilicen el
    mismo contexto de ejecucion.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Asocia el Correlation-ID a la solicitud y lo propaga a la respuesta.

        :param request: solicitud HTTP entrante.
        :param call_next: siguiente manejador de la cadena ASGI.
        :return: la respuesta con el header ``X-Correlation-ID`` presente.
        """
        incoming = request.headers.get(CORRELATION_ID_HEADER)
        correlation_id = (
            incoming.strip()
            if incoming is not None and incoming.strip()
            else generate_correlation_id()
        )

        # Disponible para logs (tarea 2.3) y clientes salientes (tarea 5.3)
        # dentro de este contexto de ejecucion.
        request.state.correlation_id = correlation_id
        token = set_correlation_id(correlation_id)
        try:
            response = await call_next(request)
        finally:
            reset_correlation_id(token)

        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response
