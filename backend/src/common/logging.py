"""Logger estructurado del Backend_FastAPI con enmascarado de PII.

Configura ``python-json-logger`` para emitir registros en formato JSON que
incluyen siempre el Correlation-ID de la solicitud en curso y que excluyen
datos sensibles: tokens, contraseñas, codigos TOTP y PII (correo, documento de
identidad). Los logs se escriben a ``stdout`` para que un recolector
centralizado los capture; nunca a archivos locales (regla de observabilidad:
logs estructurados centralizados, sin PII/secretos; R10.3).

Expone ademas la funcion pura :func:`mask_sensitive`, reutilizable por el
dashboard (R6.4) y por cualquier punto que deba enmascarar un valor sensible
dejando a lo sumo los ultimos 4 caracteres en claro.
"""

import logging
import sys
from typing import Any

from pythonjsonlogger import jsonlogger

from common.correlation import get_correlation_id

# Caracter usado para reemplazar la porcion enmascarada de un valor sensible.
_MASK_CHAR = "*"

# Numero maximo de caracteres del sufijo que se dejan en claro al enmascarar.
_MAX_CLEAR_SUFFIX = 4

# Nombre del logger raiz de la aplicacion; los modulos obtienen hijos de este.
_APP_LOGGER_NAME = "auth_2fa_poc"

# Claves cuyo valor nunca debe aparecer en los logs. Si un registro incluye una
# de estas claves en sus campos extra, su valor se reemplaza por completo.
# Allowlist invertida: ante la duda, se redacta (regla: Zero Trust / leakage).
_REDACTED_FIELDS: frozenset[str] = frozenset(
    {
        "token",
        "id_token",
        "access_token",
        "refresh_token",
        "session_cookie",
        "session",
        "password",
        "secret",
        "client_secret",
        "authorization",
        "totp",
        "otp",
        "code",
        "email",
        "correo",
        "document",
        "documento",
        "identifier",
        "identificador",
    }
)

# Marcador que sustituye por completo el valor de un campo redactado.
_REDACTED_PLACEHOLDER = "[REDACTED]"


def mask_sensitive(value: str) -> str:
    """Enmascara un valor sensible dejando a lo sumo los ultimos 4 caracteres.

    Funcion pura: para una misma entrada produce siempre la misma salida y no
    tiene efectos secundarios. Deja en claro exactamente el sufijo de longitud
    ``min(4, len(value))`` del valor original y reemplaza el resto por el
    caracter de mascara. La longitud total de la salida coincide con la de la
    entrada (R6.4, R10.3).

    Ejemplos:
        ``mask_sensitive("12345678")`` -> ``"****5678"``
        ``mask_sensitive("ab")`` -> ``"ab"``
        ``mask_sensitive("")`` -> ``""``

    :param value: cadena sensible a enmascarar (correo, documento, etc.).
    :return: la cadena con el prefijo enmascarado y a lo sumo 4 caracteres de
        sufijo en claro.
    """
    clear_suffix_length = min(_MAX_CLEAR_SUFFIX, len(value))
    masked_length = len(value) - clear_suffix_length
    return (_MASK_CHAR * masked_length) + value[len(value) - clear_suffix_length :]


class _CorrelationJsonFormatter(jsonlogger.JsonFormatter):
    """Formateador JSON que inyecta el Correlation-ID y redacta campos sensibles.

    Añade a cada registro el Correlation-ID vigente (leido del contexto de la
    solicitud) y reemplaza el valor de cualquier campo cuyo nombre figure en la
    lista de campos sensibles, evitando la fuga de tokens, contraseñas, codigos
    TOTP y PII en los logs (R10.3).
    """

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        """Completa el registro con el Correlation-ID y redacta campos sensibles.

        :param log_record: diccionario destino que se serializa a JSON.
        :param record: registro de logging original.
        :param message_dict: campos extra provistos en la llamada de logging.
        """
        super().add_fields(log_record, record, message_dict)
        log_record["correlation_id"] = get_correlation_id()
        _redact_in_place(log_record)


def _redact_in_place(log_record: dict[str, Any]) -> None:
    """Reemplaza el valor de los campos sensibles del registro por un marcador.

    La comparacion de nombres es insensible a mayusculas/minusculas para cubrir
    variantes como ``Authorization`` o ``Email``.

    :param log_record: diccionario del registro a redactar en el lugar.
    """
    for field_name in list(log_record.keys()):
        if field_name.lower() in _REDACTED_FIELDS:
            log_record[field_name] = _REDACTED_PLACEHOLDER


def configure_logging(level: int = logging.INFO) -> None:
    """Configura el logger raiz de la aplicacion con salida JSON estructurada.

    Instala un unico handler hacia ``stdout`` con el formateador que inyecta el
    Correlation-ID y redacta campos sensibles. Es idempotente: invocaciones
    repetidas no acumulan handlers duplicados.

    :param level: nivel de logging minimo a emitir (por defecto ``INFO``).
    """
    app_logger = logging.getLogger(_APP_LOGGER_NAME)
    app_logger.setLevel(level)
    # Evita la doble emision a traves del logger raiz de la libreria estandar.
    app_logger.propagate = False

    # Idempotencia: no acumular handlers si ya fue configurado.
    if app_logger.handlers:
        return

    handler = logging.StreamHandler(stream=sys.stdout)
    # Campos base del registro; el Correlation-ID se añade en add_fields.
    formatter = _CorrelationJsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
    handler.setFormatter(formatter)
    app_logger.addHandler(handler)


def get_logger(name: str | None = None) -> logging.Logger:
    """Devuelve un logger hijo del logger de la aplicacion ya configurado.

    :param name: nombre del submodulo (p. ej. ``"auth.route"``). Si es ``None``
        se devuelve el logger raiz de la aplicacion.
    :return: el ``logging.Logger`` listo para emitir registros estructurados.
    """
    if name is None:
        return logging.getLogger(_APP_LOGGER_NAME)
    return logging.getLogger(_APP_LOGGER_NAME).getChild(name)
