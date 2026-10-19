"""Manejo global de errores del Backend_FastAPI.

Principio: **errores genericos al cliente, detalle solo interno**, siempre con
el Correlation-ID de la solicitud (R10.4). Este modulo registra manejadores de
excepciones a nivel de aplicacion que:

- Capturan excepciones no controladas y responden un `ErrorResponse` generico
  (HTTP 500) sin trazas de ejecucion ni detalles de implementacion, mientras
  registran internamente el detalle completo junto con el Correlation-ID
  (R10.4). No se modifica estado: el manejador solo transforma la excepcion en
  una respuesta, de modo que no se producen cambios parciales de datos.
- Transforman los errores de validacion de entrada (Pydantic/422) en el mismo
  `ErrorResponse` generico, sin revelar que regla interna se incumplio ni el
  valor recibido (R10.7). El detalle de la validacion se registra solo
  internamente con el Correlation-ID.

Los manejadores reutilizan `get_correlation_id` (contexto de la solicitud) y el
logger estructurado con enmascarado/redaccion de PII, de modo que el detalle
interno nunca filtra tokens, credenciales ni datos personales (regla de
seguridad: leakage prevention).
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from common.api import CORRELATION_ID_HEADER
from common.correlation import (
    generate_correlation_id,
    get_correlation_id,
)
from common.logging import get_logger

# Mensajes genericos entregados al cliente. Nunca contienen detalles internos,
# trazas, nombres de campos ni valores recibidos (R10.4, R10.7).
_GENERIC_INTERNAL_ERROR = "Ocurrio un error al procesar la solicitud."
_GENERIC_VALIDATION_ERROR = "La solicitud no es valida."

# Logger del dominio de errores; emite registros estructurados con el
# Correlation-ID inyectado por el formateador (R10.3).
_logger = get_logger("common.errors")


# Marcador neutro que usa el contexto cuando aun no se asocio un Correlation-ID.
_UNSET_CORRELATION_ID = "-"


def _resolve_correlation_id(request: Request) -> str:
    """Resuelve el Correlation-ID de la solicitud para la respuesta de error.

    Un manejador de ``Exception`` a nivel de aplicacion puede ejecutarse despues
    de que el `CorrelationIdMiddleware` haya restablecido el `ContextVar` (el
    manejador de errores del servidor envuelve al middleware en la pila ASGI).
    Por eso se prioriza el valor guardado en ``request.state``, que persiste en
    el objeto de la solicitud; se recurre al contexto solo como respaldo y, si
    ninguno esta disponible, se genera un UUID v4 para garantizar que toda
    respuesta y todo registro de error lleven siempre un Correlation-ID valido
    (R10.1, R10.4).

    :param request: solicitud HTTP asociada al error.
    :return: el Correlation-ID a usar en la respuesta y el registro.
    """
    from_state = getattr(request.state, "correlation_id", None)
    if from_state:
        return from_state

    from_context = get_correlation_id()
    if from_context and from_context != _UNSET_CORRELATION_ID:
        return from_context

    return generate_correlation_id()


def _generic_error_response(
    detail: str, status_code: int, correlation_id: str
) -> JSONResponse:
    """Construye la respuesta generica de error con el Correlation-ID resuelto.

    El cuerpo cumple la forma de `ErrorResponse` (``detail`` + ``correlation_id``)
    sin incluir detalles de implementacion ni trazas (R10.4, R10.7). El
    Correlation-ID tambien se refleja en el header de respuesta, de forma
    coherente con el `CorrelationIdMiddleware`, para que el cliente pueda
    correlacionar incluso cuando el error cortocircuita la cadena del middleware.

    :param detail: mensaje generico para el cliente, sin informacion interna.
    :param status_code: codigo de estado HTTP de la respuesta.
    :param correlation_id: Correlation-ID asociado a la solicitud.
    :return: respuesta JSON con el error generico y el Correlation-ID.
    """
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail, "correlation_id": correlation_id},
        headers={CORRELATION_ID_HEADER: correlation_id},
    )


async def _handle_unhandled_exception(
    request: Request, exc: Exception
) -> JSONResponse:
    """Captura cualquier excepcion no controlada y responde de forma generica.

    Responde HTTP 500 con un `ErrorResponse` generico, sin trazas ni detalles
    de implementacion, y registra internamente el detalle del error junto con
    el Correlation-ID. El manejador no realiza mutaciones de estado, por lo que
    no se producen cambios parciales de datos (R10.4).

    :param request: solicitud HTTP que origino la excepcion.
    :param exc: excepcion no controlada capturada.
    :return: respuesta JSON generica con estado 500.
    """
    correlation_id = _resolve_correlation_id(request)
    # `exc_info=exc` deja la traza completa solo en el registro interno; nunca
    # viaja al cliente. El formateador redacta cualquier campo sensible.
    _logger.error(
        "unhandled_exception",
        exc_info=exc,
        extra={
            "resolved_correlation_id": correlation_id,
            "path": request.url.path,
            "method": request.method,
            "error_type": type(exc).__name__,
        },
    )
    return _generic_error_response(
        _GENERIC_INTERNAL_ERROR,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        correlation_id,
    )


async def _handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Transforma los errores de validacion de entrada en un error generico.

    Responde HTTP 422 con un `ErrorResponse` generico que no revela la regla
    interna incumplida ni el valor recibido (R10.7). El detalle estructurado de
    la validacion se registra unicamente de forma interna, asociado al
    Correlation-ID.

    :param request: solicitud HTTP cuya entrada no supero la validacion.
    :param exc: error de validacion de la solicitud generado por Pydantic.
    :return: respuesta JSON generica con estado 422.
    """
    correlation_id = _resolve_correlation_id(request)
    # `exc.errors()` puede contener nombres de campos y valores de entrada; se
    # mantiene solo en el registro interno, nunca en la respuesta al cliente.
    _logger.warning(
        "request_validation_error",
        extra={
            "resolved_correlation_id": correlation_id,
            "path": request.url.path,
            "method": request.method,
            "error_count": len(exc.errors()),
        },
    )
    return _generic_error_response(
        _GENERIC_VALIDATION_ERROR,
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        correlation_id,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Registra los manejadores globales de error en la aplicacion.

    Instala:

    - un manejador de ``Exception`` que convierte cualquier excepcion no
      controlada en una respuesta generica 500 (R10.4);
    - un manejador de ``RequestValidationError`` que convierte los fallos de
      validacion de entrada en una respuesta generica 422 sin filtrar la regla
      incumplida (R10.7).

    :param app: instancia de FastAPI sobre la que se registran los manejadores.
    """
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unhandled_exception)
