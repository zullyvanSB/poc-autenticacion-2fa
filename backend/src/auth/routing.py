"""Decision de enrutamiento identifier-first del Backend_FastAPI.

Contiene la funcion pura `classify_route`, que clasifica un identificador ya
normalizado (sin espacios iniciales/finales) en una de las tres rutas de
autenticacion de la PoC (R2.1, R2.2):

- Correo de dominio corporativo (match exacto contra la allowlist) ->
  ``UserType.CORPORATIVO`` con ``login_method = "google_redirect"`` (camino B,
  Google/Firebase). Se evalua **antes** del catch-all de correo cliente.
- Correo electronico valido (RFC 5322, longitud 6-254) -> ``UserType.CLIENTE``
  con ``login_method = "password_totp"`` (ruta Firebase/TOTP).
- Cadena compuesta unicamente por digitos, longitud 5-15 -> ``UserType.INTERNO``
  con ``login_method = "saml_redirect"`` (ruta SAML hacia F5 APM).

La clasificacion es una funcion pura y determinista: la misma entrada y la misma
allowlist producen siempre la misma ruta, sin efectos secundarios (sin logging
ni acceso a estado mutable). La allowlist corporativa se inyecta por parametro o
se resuelve del getter de configuracion, preservando la pureza. Esto la hace
directamente verificable por property-based testing (tarea 3.2, Property 1).

Las entradas que no corresponden a ninguno de los dos formatos no se clasifican
aqui: `classify_route` eleva `UnclassifiableIdentifierError` para que el handler
HTTP (tarea 3.3) produzca el rechazo generico sin revelar el motivo ni el valor
recibido (R2.4; regla de seguridad: leakage prevention). El error de dominio no
transporta el valor del identificador para evitar su filtracion en trazas.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from common.config import get_corporate_allowed_domains
from common.models import UserType

# Metodos de login que el frontend debe iniciar segun la ruta resuelta (R2).
LOGIN_METHOD_CLIENTE = "password_totp"
LOGIN_METHOD_INTERNO = "saml_redirect"
# Camino B (POCA-002): correo de dominio corporativo que autentica con el
# GoogleAuthProvider de Firebase (2FA delegado a Google 2-Step Verification).
LOGIN_METHOD_CORPORATIVO = "google_redirect"

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

    :ivar route_type: tipo de usuario resuelto (`CLIENTE`, `INTERNO` o
        `CORPORATIVO`).
    :ivar login_method: metodo de login que el frontend debe iniciar
        (``"password_totp"`` para Cliente, ``"saml_redirect"`` para Interno,
        ``"google_redirect"`` para Corporativo).
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


def _extract_email_domain(identifier: str) -> str:
    """Extrae el dominio (parte a la derecha del ultimo ``@``) en minusculas.

    Se espera un correo ya validado por `_is_valid_email`, que admite un unico
    ``@``; usar el ultimo ``@`` es defensivo y coincide con el unico separador.
    La normalizacion a minusculas sostiene el match case-insensitive (FR-6),
    coherente con que `get_corporate_allowed_domains` ya entrega sus entradas en
    minusculas. Funcion pura y deterministica; no registra el valor.

    :param identifier: correo electronico ya validado.
    :return: el dominio del correo normalizado a minusculas.
    """
    return identifier.rsplit("@", 1)[-1].lower()


def _is_corporate_email(identifier: str, allowed_domains: Sequence[str]) -> bool:
    """Indica si `identifier` es un correo corporativo de la allowlist (FR-5, FR-6).

    Devuelve ``True`` solo si el identificador es un correo valido y su dominio
    es **igual exacto** (no sufijo ni subdominio) a alguna entrada de
    `allowed_domains`. El match por igualdad —no ``endswith``— evita ampliar la
    superficie de dominios permitidos via subdominios arbitrarios
    (``mail.segurosbolivar.com`` no coincide con ``segurosbolivar.com``).
    Funcion pura y deterministica; no hace I/O ni logging.

    :param identifier: identificador normalizado a evaluar.
    :param allowed_domains: dominios corporativos permitidos (en minusculas).
    :return: ``True`` si es correo valido y su dominio coincide exactamente.
    """
    if not _is_valid_email(identifier):
        return False
    return _extract_email_domain(identifier) in allowed_domains


def classify_route(
    identifier: str,
    allowed_corporate_domains: Sequence[str] | None = None,
) -> RouteClassification:
    """Clasifica un identificador en la ruta de autenticacion correspondiente.

    Funcion pura y determinista (R2.1, R2.2; Property 1): la misma entrada y la
    misma allowlist producen siempre la misma salida, sin efectos secundarios.
    Se espera un identificador ya normalizado (sin espacios iniciales/finales),
    tal como lo entrega `RouteRequest` con ``str_strip_whitespace=True``.

    Orden de evaluacion (la primera coincidencia gana):

    1. Correo corporativo (dominio en la allowlist, match exacto) -> `CORPORATIVO`
       / ``"google_redirect"`` (camino B).
    2. Correo electronico valido (RFC 5322, 6-254) -> `CLIENTE` /
       ``"password_totp"`` (camino C, catch-all de correo).
    3. Cadena de solo digitos (5-15) -> `INTERNO` / ``"saml_redirect"`` (camino A).

    La discriminacion corporativa precede **estrictamente** al catch-all de
    correo cliente (FR-2): un correo de dominio corporativo nunca resuelve
    `CLIENTE`. El match de dominio es exacto (FR-5) y case-insensitive (FR-6);
    un subdominio de un dominio permitido resuelve `CLIENTE`.

    La allowlist se resuelve al inicio de la llamada: si se inyecta
    `allowed_corporate_domains` se usa tal cual (testabilidad y pureza sin tocar
    ``os.environ``); si es ``None`` se obtiene del getter
    `get_corporate_allowed_domains` (default de produccion, fail-safe a ``[]``).
    Con allowlist vacia, ningun correo se clasifica como corporativo y el camino
    C queda intacto (FR-9). La funcion no registra el identificador ni el
    dominio (leakage prevention); el logging vive en el handler.

    :param identifier: identificador normalizado a clasificar.
    :param allowed_corporate_domains: allowlist de dominios corporativos (en
        minusculas). Si es ``None``, se resuelve del getter de configuracion.
    :return: la clasificacion con el tipo de usuario y el metodo de login.
    :raises UnclassifiableIdentifierError: si el identificador no corresponde a
        un correo valido ni a un numero de identificacion valido. El error no
        incluye el valor recibido (R2.4; leakage prevention).
    """
    domains = (
        allowed_corporate_domains
        if allowed_corporate_domains is not None
        else get_corporate_allowed_domains()
    )
    if _is_corporate_email(identifier, domains):
        return RouteClassification(
            route_type=UserType.CORPORATIVO,
            login_method=LOGIN_METHOD_CORPORATIVO,
        )
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
