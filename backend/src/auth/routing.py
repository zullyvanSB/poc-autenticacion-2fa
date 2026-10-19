"""Decision de enrutamiento identifier-first del Backend_FastAPI.

Contiene la funcion pura `classify_route`, que clasifica un identificador ya
normalizado (sin espacios iniciales/finales) en una de las dos rutas de
autenticacion de la PoC (R2.1, R2.2):

- Correo electronico valido (RFC 5322, longitud 6-254) -> ``UserType.CLIENTE``
  con ``login_method = "password_totp"`` (ruta Firebase/TOTP).
- Cadena compuesta unicamente por digitos, longitud 5-15 -> ``UserType.INTERNO``
  con ``login_method = "saml_redirect"`` (ruta SAML hacia F5 APM).

La clasificacion es una funcion pura y determinista: la misma entrada produce
siempre la misma ruta, sin efectos secundarios (sin logging ni acceso a
estado). Esto la hace directamente verificable por property-based testing
(tarea 3.2, Property 1).

Las entradas que no corresponden a ninguno de los dos formatos no se clasifican
aqui: `classify_route` eleva `UnclassifiableIdentifierError` para que el handler
HTTP (tarea 3.3) produzca el rechazo generico sin revelar el motivo ni el valor
recibido (R2.4; regla de seguridad: leakage prevention). El error de dominio no
transporta el valor del identificador para evitar su filtracion en trazas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from common.models import UserType

# Metodos de login que el frontend debe iniciar segun la ruta resuelta (R2).
LOGIN_METHOD_CLIENTE = "password_totp"
LOGIN_METHOD_INTERNO = "saml_redirect"

# Longitudes permitidas por formato, conforme a los criterios de aceptacion.
# Correo electronico (R2.1): 6-254 caracteres.
_EMAIL_MIN_LENGTH = 6
_EMAIL_MAX_LENGTH = 254
# Numero de identificacion (R2.2): 5-15 digitos.
_ID_MIN_LENGTH = 5
_ID_MAX_LENGTH = 15

# Numero de identificacion: exclusivamente digitos ASCII (R2.2). Se evita
# ``str.isdigit``, que acepta digitos Unicode no ASCII (p. ej. superindices).
_IDENTIFICATION_PATTERN = re.compile(r"\A[0-9]+\Z")

# Validacion de correo electronico informada por RFC 5322, acotada de forma
# pragmatica y deterministica (sin dependencias externas). Un unico ``@``
# separa la parte local del dominio:
#   - Parte local: uno o mas "atoms" de caracteres permitidos (dot-atom),
#     separados por puntos simples; sin punto inicial, final ni consecutivo.
#   - Dominio: etiquetas alfanumericas (con guiones internos) separadas por
#     puntos, con un TLD alfabetico de al menos dos caracteres.
# No se admiten comillas, comentarios ni literales de dominio (direcciones IP):
# formas raras de RFC 5322 no aplicables a los identificadores de la PoC.
_LOCAL_ATOM = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
_EMAIL_PATTERN = re.compile(
    r"\A"
    rf"{_LOCAL_ATOM}(?:\.{_LOCAL_ATOM})*"
    r"@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,}"
    r"\Z"
)


class UnclassifiableIdentifierError(ValueError):
    """El identificador no corresponde a correo valido ni a numero de identificacion.

    Se eleva cuando `classify_route` no puede asignar una ruta. No transporta el
    valor del identificador para evitar filtrarlo en trazas o logs; el handler
    HTTP lo traduce a un `ErrorResponse` generico asociado al Correlation-ID
    (R2.4, R10.7; regla de seguridad: leakage prevention).
    """


@dataclass(frozen=True)
class RouteClassification:
    """Resultado inmutable de la clasificacion de enrutamiento (R2.1, R2.2).

    :ivar route_type: tipo de usuario resuelto (`CLIENTE` o `INTERNO`).
    :ivar login_method: metodo de login que el frontend debe iniciar
        (``"password_totp"`` para Cliente, ``"saml_redirect"`` para Interno).
    """

    route_type: UserType
    login_method: str


def _is_valid_email(identifier: str) -> bool:
    """Indica si `identifier` es un correo electronico valido para la PoC.

    Aplica la restriccion de longitud (6-254, R2.1) y el patron de formato
    informado por RFC 5322. Funcion pura y deterministica.

    :param identifier: identificador normalizado (sin espacios externos).
    :return: ``True`` si cumple longitud y formato de correo; ``False`` si no.
    """
    if not _EMAIL_MIN_LENGTH <= len(identifier) <= _EMAIL_MAX_LENGTH:
        return False
    return _EMAIL_PATTERN.match(identifier) is not None


def _is_valid_identification(identifier: str) -> bool:
    """Indica si `identifier` es un numero de identificacion valido para la PoC.

    Aplica la restriccion de longitud (5-15, R2.2) y exige exclusivamente
    digitos ASCII. Funcion pura y deterministica.

    :param identifier: identificador normalizado (sin espacios externos).
    :return: ``True`` si cumple longitud y formato numerico; ``False`` si no.
    """
    if not _ID_MIN_LENGTH <= len(identifier) <= _ID_MAX_LENGTH:
        return False
    return _IDENTIFICATION_PATTERN.match(identifier) is not None


def classify_route(identifier: str) -> RouteClassification:
    """Clasifica un identificador en la ruta de autenticacion correspondiente.

    Funcion pura y determinista (R2.1, R2.2; Property 1): la misma entrada
    produce siempre la misma salida y no tiene efectos secundarios. Se espera un
    identificador ya normalizado (sin espacios iniciales/finales), tal como lo
    entrega `RouteRequest` con ``str_strip_whitespace=True``.

    - Correo electronico valido (RFC 5322, 6-254) -> `CLIENTE` /
      ``"password_totp"``.
    - Cadena de solo digitos (5-15) -> `INTERNO` / ``"saml_redirect"``.

    El formato de correo se evalua antes que el numerico; ambos conjuntos son
    disjuntos (un correo contiene ``@``, un numero de identificacion no), por lo
    que el orden no afecta el resultado.

    :param identifier: identificador normalizado a clasificar.
    :return: la clasificacion con el tipo de usuario y el metodo de login.
    :raises UnclassifiableIdentifierError: si el identificador no corresponde a
        un correo valido ni a un numero de identificacion valido. El error no
        incluye el valor recibido (R2.4; leakage prevention).
    """
    if _is_valid_email(identifier):
        return RouteClassification(
            route_type=UserType.CLIENTE,
            login_method=LOGIN_METHOD_CLIENTE,
        )
    if _is_valid_identification(identifier):
        return RouteClassification(
            route_type=UserType.INTERNO,
            login_method=LOGIN_METHOD_INTERNO,
        )
    raise UnclassifiableIdentifierError(
        "El identificador no corresponde a un formato clasificable."
    )
