"""Endpoint del recurso protegido del Backend_FastAPI (`GET /api/v1/dashboard`).

Expone el `APIRouter` del dominio del dashboard con el handler que valida la
sesion y arma la respuesta protegida (R6). El handler:

- Lee la session cookie por su nombre configurado (`get_session_cookie_name`)
  y la valida con `verify_session_cookie`, que delega en el Admin SDK con
  `check_revoked=True` (R6.2). Toda la autorizacion es server-side: nunca se
  confia en identificadores del cliente ni en estado del navegador (regla de
  seguridad: BOLA/autorizacion server-side).
- Ante sesion ausente, invalida, expirada o revocada, responde un
  `ErrorResponse` generico HTTP 401 SIN incluir ningun dato del dashboard y sin
  modificar la sesion (R6.3; Property 6). Ninguna respuesta 401 contiene claims
  ni nombre de usuario.
- Con sesion valida, deriva el tipo de usuario (`derive_user_type`) y construye
  un `DashboardResponse` con `display_name`, `user_type` y los claims mapeados a
  `MaskedClaim`, aplicando una allowlist de campos y enmascarando los valores
  sensibles (correo, documento) a lo sumo a sus ultimos 4 caracteres con
  `mask_sensitive` (R6.4). Nunca se serializa el objeto de token completo (regla
  de seguridad: leakage prevention; DTO allowlist). Si no hay claims
  presentables, `claims` queda vacio para que el frontend muestre la ausencia de
  datos (R6.6).

El handler se mantiene testeable sin proveedores reales: el Admin SDK se
mockea en las pruebas unitarias (tarea 7.2) y aqui solo se orquesta la
validacion y el armado del DTO.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from auth.firebase_auth import (
    FirebaseAuthError,
    InvalidSessionCookieError,
    UnknownSignInProviderError,
    derive_user_type,
    verify_session_cookie,
)
from common.api import CORRELATION_ID_HEADER
from common.config import get_session_cookie_name
from common.correlation import generate_correlation_id, get_correlation_id
from common.logging import get_logger, mask_sensitive
from common.models import ErrorResponse
from dashboard.models import DashboardResponse, MaskedClaim

# Mensaje generico de no autorizado entregado al cliente. No revela si la sesion
# estaba ausente, era invalida, estaba expirada o fue revocada (R6.3, R10.4).
_GENERIC_UNAUTHORIZED = "No autorizado."

# Marcador neutro que usa el contexto cuando aun no se asocio un Correlation-ID.
_UNSET_CORRELATION_ID = "-"

# Nombre por defecto a mostrar cuando el token no trae un nombre presentable.
# No es PII: es un marcador neutro para no exponer campos vacios sin indicacion.
_DEFAULT_DISPLAY_NAME = "Usuario"

# Allowlist de claims presentables en el dashboard. Solo estos nombres de claim
# del token verificado pueden salir al cliente; cualquier otro (secretos
# internos, metadatos del proveedor) se descarta (DTO allowlist; R6.4).
_PRESENTABLE_CLAIMS: tuple[str, ...] = (
    "name",
    "email",
    "document",
    "documento",
    "user_type",
)

# Subconjunto de la allowlist cuyo valor es PII sensible y debe enmascararse a
# lo sumo a sus ultimos 4 caracteres antes de salir del backend (R6.4, R10.3).
_SENSITIVE_CLAIMS: frozenset[str] = frozenset(
    {"email", "document", "documento"}
)

# Claim del que se deriva el nombre a mostrar en el dashboard.
_DISPLAY_NAME_CLAIM = "name"

# Para usuarios Interno (SAML), Identity Platform NO puebla los claims
# top-level `email`/`name` cuando el IdP envia el correo como ATRIBUTO (no en
# el NameID) y un NameID `persistent`. En su lugar, expone todos los atributos
# SAML crudos bajo `firebase.sign_in_attributes` (doc oficial de Identity
# Platform). El IdP de F5 APM envia el correo y el nombre con las URIs de
# WS-Federation/SAML de abajo. Se leen de ahi para presentar correo y nombre
# tambien en la ruta Interno, sin alterar la ruta Cliente (que si trae los
# claims top-level).
_FIREBASE_CLAIM = "firebase"
_SIGN_IN_ATTRIBUTES_CLAIM = "sign_in_attributes"
_SAML_ATTR_MAIL = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/mail"
_SAML_ATTR_FULLNAME = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/fullName"
_SAML_ATTR_GIVENNAME = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname"
_SAML_ATTR_SURNAME = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/sn"

# Logger del dominio del dashboard; el formateador inyecta el Correlation-ID y
# redacta campos sensibles en cada registro (R10.3).
_logger = get_logger("dashboard")

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _resolve_correlation_id(request: Request) -> str:
    """Resuelve el Correlation-ID vigente para la respuesta y el registro.

    Prioriza el valor que el `CorrelationIdMiddleware` guardo en
    ``request.state``; recurre al `ContextVar` compartido como respaldo y, si
    ninguno esta disponible, genera un UUID v4 para garantizar que toda
    respuesta y todo registro lleven siempre un Correlation-ID valido (R10.1).

    :param request: solicitud HTTP asociada a la peticion del dashboard.
    :return: el Correlation-ID a usar en la respuesta y los registros.
    """
    from_state = getattr(request.state, "correlation_id", None)
    if from_state:
        return from_state

    from_context = get_correlation_id()
    if from_context and from_context != _UNSET_CORRELATION_ID:
        return from_context

    return generate_correlation_id()


def _unauthorized_response(correlation_id: str) -> JSONResponse:
    """Construye la respuesta 401 generica de no autorizado del dashboard.

    Centraliza la respuesta de denegacion para garantizar un cuerpo uniforme y
    generico (`ErrorResponse`) que NO incluye ningun dato del dashboard (ni
    claims ni nombre) y que no revela el motivo del fallo (R6.3, R10.4;
    Property 6). No modifica ni reemite la cookie de sesion.

    :param correlation_id: Correlation-ID vigente para la respuesta y el log.
    :return: respuesta JSON 401 con `ErrorResponse` generico y Correlation-ID.
    """
    error = ErrorResponse(detail=_GENERIC_UNAUTHORIZED, correlation_id=correlation_id)
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content=error.model_dump(),
        headers={CORRELATION_ID_HEADER: correlation_id},
    )


def _get_sign_in_attributes(claims: dict[str, Any]) -> dict[str, Any]:
    """Devuelve el diccionario `firebase.sign_in_attributes`, o vacio si no hay.

    Para la ruta Interno (SAML), Identity Platform expone los atributos de la
    asercion bajo `firebase.sign_in_attributes`. Para la ruta Cliente este
    objeto no existe y se devuelve un diccionario vacio.

    :param claims: claims decodificados de una sesion verificada.
    :return: el mapa de atributos SAML, o un diccionario vacio.
    """
    firebase_claim = claims.get(_FIREBASE_CLAIM)
    if not isinstance(firebase_claim, dict):
        return {}
    attributes = firebase_claim.get(_SIGN_IN_ATTRIBUTES_CLAIM)
    return attributes if isinstance(attributes, dict) else {}


def _read_saml_attribute(attributes: dict[str, Any], key: str) -> str | None:
    """Lee un atributo SAML como string, tolerando valores en lista.

    Los atributos SAML pueden llegar como string o como lista de strings
    (multivaluados). Se devuelve el primer valor no vacio como texto, o
    ``None`` si el atributo esta ausente o vacio.

    :param attributes: mapa `firebase.sign_in_attributes`.
    :param key: URI del atributo SAML a leer.
    :return: el valor como string no vacio, o ``None``.
    """
    raw = attributes.get(key)
    if isinstance(raw, list):
        raw = next((v for v in raw if v not in (None, '')), None)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _build_masked_claims(claims: dict[str, Any]) -> list[MaskedClaim]:
    """Construye la lista de claims presentables aplicando allowlist y enmascarado.

    Recorre unicamente los nombres de la allowlist `_PRESENTABLE_CLAIMS`: todo
    claim fuera de ella se descarta para no filtrar datos internos (DTO
    allowlist; R6.4). Para los claims sensibles (`_SENSITIVE_CLAIMS`) enmascara
    el valor con `mask_sensitive`, dejando a lo sumo sus ultimos 4 caracteres en
    claro (R6.4, R10.3). Los valores no string se normalizan a texto antes de
    incluirse. Si no hay claims presentables, devuelve una lista vacia para que
    el frontend muestre la ausencia de datos (R6.6).

    :param claims: diccionario de claims decodificados de una sesion verificada.
    :return: lista de `MaskedClaim` presentables, posiblemente vacia.
    """
    masked: list[MaskedClaim] = []
    for name in _PRESENTABLE_CLAIMS:
        if name not in claims:
            continue
        raw_value = claims[name]
        if raw_value is None:
            continue
        value = str(raw_value)
        if value == "":
            continue
        if name in _SENSITIVE_CLAIMS:
            value = mask_sensitive(value)
        masked.append(MaskedClaim(name=name, value=value))

    # Ruta Interno (SAML): si el correo no vino como claim top-level, tomarlo
    # del atributo SAML `mail` y presentarlo enmascarado como un claim `email`
    # (mismo trato de PII que la ruta Cliente, R6.4). No se duplica si ya se
    # agrego un `email` top-level.
    already_has_email = any(c.name == "email" for c in masked)
    if not already_has_email:
        attributes = _get_sign_in_attributes(claims)
        saml_email = _read_saml_attribute(attributes, _SAML_ATTR_MAIL)
        if saml_email is not None:
            masked.append(MaskedClaim(name="email", value=mask_sensitive(saml_email)))
    return masked


def _derive_display_name(claims: dict[str, Any]) -> str:
    """Deriva el nombre a mostrar en el dashboard desde los claims verificados.

    Usa el claim `name` cuando esta presente y no vacio; de lo contrario,
    devuelve un marcador neutro para no exponer un campo vacio sin indicacion
    (R6.6). El nombre a mostrar no es un identificador sensible como el correo o
    el documento, por lo que no se enmascara.

    :param claims: diccionario de claims decodificados de una sesion verificada.
    :return: el nombre a mostrar, nunca vacio.
    """
    raw_name = claims.get(_DISPLAY_NAME_CLAIM)
    if isinstance(raw_name, str) and raw_name.strip():
        return raw_name

    # Ruta Interno (SAML): el nombre llega como atributo `fullName` (o la
    # combinacion givenname + sn). El nombre a mostrar no es PII sensible como
    # el correo o el documento, por lo que no se enmascara.
    attributes = _get_sign_in_attributes(claims)
    full_name = _read_saml_attribute(attributes, _SAML_ATTR_FULLNAME)
    if full_name is not None:
        return full_name
    given = _read_saml_attribute(attributes, _SAML_ATTR_GIVENNAME)
    surname = _read_saml_attribute(attributes, _SAML_ATTR_SURNAME)
    combined = " ".join(part for part in (given, surname) if part)
    if combined:
        return combined
    return _DEFAULT_DISPLAY_NAME


@router.get(
    "",
    summary="Devuelve los datos del recurso protegido (dashboard)",
    responses={
        status.HTTP_200_OK: {"model": DashboardResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
    },
)
def get_dashboard(request: Request) -> JSONResponse:
    """Valida la sesion y arma la respuesta del recurso protegido (R6).

    1. Lee la session cookie por su nombre configurado. Si falta, responde
       ``401`` generico sin datos y sin modificar la sesion (R6.3; Property 6).
    2. Valida la cookie con `verify_session_cookie` (`check_revoked=True`;
       R6.2). Si la validacion falla (invalida, expirada o revocada), responde
       ``401`` generico sin datos (R6.3; Property 6).
    3. Con sesion valida, deriva el `UserType` (`derive_user_type`) y construye
       el `DashboardResponse` con `display_name`, `user_type` y los claims
       presentables mapeados a `MaskedClaim`, enmascarando el correo y el
       documento a lo sumo a sus ultimos 4 caracteres (R6.4). Si no hay claims
       presentables, `claims` queda vacio (R6.6).

    Ninguna respuesta ``401`` incluye claims ni nombre de usuario. La cookie ni
    los claims crudos se registran: solo eventos con el Correlation-ID (R10.3).

    :param request: solicitud HTTP, portadora de la cookie y del Correlation-ID.
    :return: respuesta JSON ``200`` con `DashboardResponse` (sesion valida) o
        ``401`` generica sin datos (sesion ausente, invalida o revocada).
    """
    correlation_id = _resolve_correlation_id(request)
    cookie_name = get_session_cookie_name()
    session_cookie = request.cookies.get(cookie_name)

    # Sesion ausente: 401 sin datos, sin tocar la sesion (R6.3; Property 6).
    if not session_cookie:
        _logger.info(
            "dashboard_denied_no_session",
            extra={"resolved_correlation_id": correlation_id},
        )
        return _unauthorized_response(correlation_id)

    # Validacion server-side de la cookie (R6.2). Cualquier fallo deniega sin
    # filtrar datos ni la causa concreta (R6.3; Property 6). La cookie nunca se
    # registra: la verificacion ya registra solo el tipo de error internamente.
    try:
        claims = verify_session_cookie(session_cookie)
    except (InvalidSessionCookieError, FirebaseAuthError):
        _logger.info(
            "dashboard_denied_invalid_session",
            extra={"resolved_correlation_id": correlation_id},
        )
        return _unauthorized_response(correlation_id)

    # Derivar el tipo de usuario desde el proveedor de ingreso del token. Un
    # proveedor desconocido se trata como no autorizado (no se filtran datos).
    try:
        user_type = derive_user_type(claims)
    except UnknownSignInProviderError:
        _logger.info(
            "dashboard_denied_unknown_provider",
            extra={"resolved_correlation_id": correlation_id},
        )
        return _unauthorized_response(correlation_id)

    display_name = _derive_display_name(claims)
    masked_claims = _build_masked_claims(claims)

    dashboard = DashboardResponse(
        display_name=display_name,
        user_type=user_type,
        user_type_label=user_type.label,
        claims=masked_claims,
    )

    _logger.info(
        "dashboard_served",
        extra={
            "resolved_correlation_id": correlation_id,
            "user_type": user_type.value,
            "claims_count": len(masked_claims),
        },
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=dashboard.model_dump(),
        headers={CORRELATION_ID_HEADER: correlation_id},
    )
