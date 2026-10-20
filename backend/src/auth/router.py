"""Endpoints del dominio de autenticacion del Backend_FastAPI.

Expone el `APIRouter` del dominio de autenticacion con los handlers de:

- `POST /api/v1/auth/route`: decide la ruta Cliente/Interno (R2).
- `POST /api/v1/auth/session`: crea la sesion server-side desde el ID token (R5).
- `POST /api/v1/auth/logout`: cierra la sesion de forma idempotente (R6.5).

El handler de enrutamiento (`/route`):

- Recibe un `RouteRequest` ya validado server-side por Pydantic (tipo, longitud
  5-254, `extra="forbid"`); los fallos de esquema los traduce el manejador
  global de validacion a un 422 generico (R10.6, R10.7).
- Clasifica el identificador con la funcion pura `classify_route` y devuelve un
  `RouteResponse` con la ruta, el metodo de login y el Correlation-ID (R2.1,
  R2.2).
- Ante un identificador con esquema valido pero no clasificable (ni correo ni
  numero de identificacion), responde un `ErrorResponse` generico HTTP 400 que
  no revela el motivo ni el valor recibido (R2.4, R1.3, R1.4, R10.7).
- Registra tanto la decision de ruta como el rechazo asociados al Correlation-ID,
  excluyendo siempre el valor del identificador (R2.5; leakage prevention).

El handler no ejecuta la autenticacion: solo clasifica y le indica al frontend
que metodo de login iniciar. No tiene efectos de red ni de estado, lo que lo
mantiene testeable con `TestClient`/`httpx` sin proveedores reales.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse

from typing import Any

from auth import firebase_auth
from auth.firebase_auth import (
    _FIREBASE_CLAIM,
    _PROVIDER_GOOGLE,
    _SIGN_IN_PROVIDER_CLAIM,
    FirebaseAuthError,
    InvalidIdTokenError,
    SessionCookieError,
    SessionRevocationError,
    create_session_cookie,
    is_allowed_corporate_email,
    revoke_session,
    verify_id_token,
)
from auth.models import RouteRequest, RouteResponse, SessionRequest
from auth.routing import UnclassifiableIdentifierError, classify_route
from common.api import CORRELATION_ID_HEADER
from common.config import (
    get_corporate_allowed_domains,
    get_session_cookie_max_age_seconds,
    get_session_cookie_name,
    get_session_cookie_samesite,
    get_session_cookie_secure,
)
from common.correlation import generate_correlation_id, get_correlation_id
from common.logging import get_logger
from common.models import ErrorResponse
from common.rate_limit import InMemoryRateLimiter, build_rate_limit_key

# Mensaje generico de rechazo entregado al cliente. No revela si el fallo fue de
# longitud, formato de correo o formato numerico, ni incluye el valor recibido
# (R2.4, R10.7).
_GENERIC_INVALID_IDENTIFIER = "El identificador proporcionado no es valido."

# Mensaje generico de fallo de autenticacion para el endpoint de sesion. No
# revela si el token era invalido, estaba expirado o si fallo la creacion de la
# cookie, ni incluye el token ni detalles de implementacion (R5.4, R5.5, R10.4).
_GENERIC_AUTH_FAILURE = "No fue posible autenticar la solicitud."

# Mensaje generico entregado al superar el rate limiting del endpoint. No revela
# el limite configurado, el conteo ni la ventana: solo indica demasiadas
# solicitudes, acompanado de la cabecera `Retry-After` (R10.4, R10.7).
_GENERIC_RATE_LIMITED = "Demasiadas solicitudes. Intentelo de nuevo mas tarde."

# Ambitos de rate limiting por endpoint reforzado. Separan el conteo por
# endpoint para que el trafico de uno no agote el cupo del otro (R3.7, R4.10).
_RATE_LIMIT_SCOPE_ROUTE = "auth.route"
_RATE_LIMIT_SCOPE_SESSION = "auth.session"

# Claim del uid del usuario en los claims de una session cookie verificada. El
# Admin SDK expone el identificador del sujeto bajo las claves `uid` y `sub`.
_UID_CLAIM = "uid"
_SUBJECT_CLAIM = "sub"

# Claim top-level `email` de un ID token de Google (a diferencia del SAML, que
# trae el correo como atributo). Es el ancla de la autorizacion corporativa del
# camino B: la validacion de dominio se apoya en este claim, nunca en el `hd`
# que provee el frontend (cosmetico y eludible; POCA-004).
_EMAIL_CLAIM = "email"


def _is_google_provider(claims: dict[str, Any]) -> bool:
    """Indica si los claims verificados corresponden al proveedor Google.

    Lee ``firebase.sign_in_provider`` con la misma guarda defensiva que
    `derive_user_type` (el objeto ``firebase`` puede faltar o no ser ``dict``) y
    compara por igualdad EXACTA contra ``_PROVIDER_GOOGLE`` (``"google.com"``).
    Un token de otro proveedor (``password``, ``saml.<id>``) o sin objeto
    ``firebase`` resoluble devuelve ``False``, de modo que la validacion de
    dominio del camino B no se aplique y el flujo existente quede intacto
    (FR-2, FR-9; No-Objetivos).

    :param claims: claims decodificados de un token ya verificado.
    :return: ``True`` solo si el proveedor es exactamente ``"google.com"``.
    """
    firebase_claim = claims.get(_FIREBASE_CLAIM)
    if not isinstance(firebase_claim, dict):
        return False
    return firebase_claim.get(_SIGN_IN_PROVIDER_CLAIM) == _PROVIDER_GOOGLE

# Marcador neutro que usa el contexto cuando aun no se asocio un Correlation-ID.
_UNSET_CORRELATION_ID = "-"

# Logger del dominio de enrutamiento; el formateador inyecta el Correlation-ID y
# redacta campos sensibles en cada registro (R10.3).
_logger = get_logger("auth.route")

router = APIRouter(prefix="/auth", tags=["auth"])


def _resolve_correlation_id(request: Request) -> str:
    """Resuelve el Correlation-ID vigente para la respuesta y el registro.

    Prioriza el valor que el `CorrelationIdMiddleware` guardo en
    ``request.state``; recurre al `ContextVar` compartido como respaldo y, si
    ninguno esta disponible, genera un UUID v4 para garantizar que toda
    respuesta y todo registro lleven siempre un Correlation-ID valido (R2.6,
    R10.1).

    :param request: solicitud HTTP asociada al enrutamiento.
    :return: el Correlation-ID a usar en la respuesta y los registros.
    """
    from_state = getattr(request.state, "correlation_id", None)
    if from_state:
        return from_state

    from_context = get_correlation_id()
    if from_context and from_context != _UNSET_CORRELATION_ID:
        return from_context

    return generate_correlation_id()


def _enforce_rate_limit(
    request: Request, scope: str, correlation_id: str
) -> JSONResponse | None:
    """Aplica el refuerzo de rate limiting del endpoint y, si procede, lo rechaza.

    Lee el `InMemoryRateLimiter` compartido de ``request.app.state`` (lo monta
    `create_app`). Es un **refuerzo** de los bloqueos primarios de Firebase
    (R3.7) y F5 (R4.10): si el limitador no esta disponible (p. ej. en arneses
    de prueba que no lo montan), no bloquea el flujo y devuelve ``None``.

    Construye la clave por ambito del endpoint y por cliente (IP de origen) y
    evalua la solicitud. Si se supera el limite, registra el evento con el
    Correlation-ID (sin IP ni PII) y devuelve un ``429`` generico con la
    cabecera ``Retry-After``, sin revelar el limite ni la ventana (R10.4,
    R10.7). Si la solicitud esta dentro del limite, devuelve ``None`` para que
    el handler continue.

    :param request: solicitud HTTP; aporta la IP del cliente y el limitador.
    :param scope: identificador del endpoint reforzado (``auth.route`` /
        ``auth.session``).
    :param correlation_id: Correlation-ID vigente para la respuesta y el log.
    :return: una respuesta ``429`` si se supero el limite; ``None`` en caso
        contrario.
    """
    limiter = getattr(request.app.state, "rate_limiter", None)
    if not isinstance(limiter, InMemoryRateLimiter):
        # Sin limitador montado el refuerzo se omite; los bloqueos primarios de
        # Firebase y F5 siguen vigentes. No se interrumpe el flujo.
        return None

    client_host = request.client.host if request.client is not None else None
    key = build_rate_limit_key(scope, client_host)
    decision = limiter.check(key)
    if decision.allowed:
        return None

    # Rechazo generico 429: no se registra la IP ni el valor de la clave, solo
    # el ambito y el Correlation-ID para trazabilidad (R10.3, R10.4).
    _logger.warning(
        "rate_limit_exceeded",
        extra={
            "resolved_correlation_id": correlation_id,
            "rate_limit_scope": scope,
        },
    )
    error = ErrorResponse(detail=_GENERIC_RATE_LIMITED, correlation_id=correlation_id)
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content=error.model_dump(),
        headers={
            CORRELATION_ID_HEADER: correlation_id,
            "Retry-After": str(decision.retry_after_seconds),
        },
    )


@router.post(
    "/route",
    summary="Decide la ruta de autenticacion (Cliente o Interno)",
    responses={
        status.HTTP_200_OK: {"model": RouteResponse},
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"model": ErrorResponse},
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse},
    },
)
def resolve_route(request: Request, payload: RouteRequest) -> JSONResponse:
    """Resuelve la ruta de autenticacion a partir del identificador (R2).

    Clasifica el identificador normalizado con `classify_route` y responde la
    ruta resuelta. No ejecuta la autenticacion ni realiza llamadas salientes:
    solo indica al frontend que metodo de login iniciar.

    - Exito: ``200`` con `RouteResponse` (``route_type``, ``login_method``,
      ``correlation_id``). Registra la decision asociada al Correlation-ID sin
      el valor del identificador (R2.5).
    - Identificador no clasificable (esquema valido pero ni correo ni numero de
      identificacion): ``400`` con `ErrorResponse` generico que no revela el
      motivo ni el valor recibido; registra el rechazo con el Correlation-ID sin
      el identificador (R2.4, R1.3, R1.4, R10.7).

    Los fallos de esquema de `RouteRequest` (tipo, longitud 5-254, claves
    inesperadas) no llegan a este cuerpo: los traduce el manejador global de
    validacion a un ``422`` generico (R10.6, R10.7).

    :param request: solicitud HTTP, portadora del Correlation-ID de contexto.
    :param payload: cuerpo validado con el identificador a clasificar.
    :return: respuesta JSON con la ruta resuelta (200) o el error generico (400).
    """
    correlation_id = _resolve_correlation_id(request)

    # Refuerzo de rate limiting del endpoint de enrutamiento (R3.7, R4.10). Si
    # se supera el limite, se rechaza con 429 generico antes de clasificar.
    rate_limited = _enforce_rate_limit(
        request, _RATE_LIMIT_SCOPE_ROUTE, correlation_id
    )
    if rate_limited is not None:
        return rate_limited

    try:
        classification = classify_route(payload.identifier)
    except UnclassifiableIdentifierError:
        # El error de dominio no transporta el identificador; se registra el
        # rechazo con el Correlation-ID y sin el valor recibido (R2.4, R2.5).
        _logger.warning(
            "route_identifier_rejected",
            extra={"resolved_correlation_id": correlation_id},
        )
        error = ErrorResponse(
            detail=_GENERIC_INVALID_IDENTIFIER,
            correlation_id=correlation_id,
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=error.model_dump(),
            headers={CORRELATION_ID_HEADER: correlation_id},
        )

    # Registro de la decision de ruta: incluye el tipo resuelto y el metodo de
    # login, nunca el valor del identificador ni PII (R2.5; leakage prevention).
    _logger.info(
        "route_resolved",
        extra={
            "resolved_correlation_id": correlation_id,
            "route_type": classification.route_type.value,
            "login_method": classification.login_method,
        },
    )
    route_response = RouteResponse(
        route_type=classification.route_type,
        login_method=classification.login_method,
        correlation_id=correlation_id,
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=route_response.model_dump(),
        headers={CORRELATION_ID_HEADER: correlation_id},
    )


def _auth_failure_response(correlation_id: str) -> JSONResponse:
    """Construye la respuesta 401 generica de fallo de autenticacion de sesion.

    Centraliza la respuesta de denegacion para garantizar un cuerpo uniforme y
    generico (`ErrorResponse`) que no revela el motivo del fallo (token invalido
    o expirado, fallo de creacion de cookie) ni establece ninguna sesion
    (R5.4, R5.5, R10.4; Property 4). Nunca incluye cabecera ``Set-Cookie``.

    :param correlation_id: Correlation-ID vigente para la respuesta y el log.
    :return: respuesta JSON 401 con `ErrorResponse` generico y Correlation-ID.
    """
    error = ErrorResponse(detail=_GENERIC_AUTH_FAILURE, correlation_id=correlation_id)
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content=error.model_dump(),
        headers={CORRELATION_ID_HEADER: correlation_id},
    )


@router.post(
    "/session",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Crea la sesion server-side a partir del Firebase ID token",
    responses={
        status.HTTP_204_NO_CONTENT: {
            "description": (
                "Sesion creada. La session cookie viaja en el header Set-Cookie "
                "(Secure, HttpOnly, SameSite=Strict, Path=/, Max-Age acotado); "
                "el cuerpo de la respuesta esta vacio."
            )
        },
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse},
    },
)
def create_session(request: Request, payload: SessionRequest) -> Response:
    """Crea la sesion server-side verificando el Firebase ID token (R5).

    Materializa la parte server-side del flujo de sesion:

    1. Verifica el ID token con `verify_id_token` (firma, emisor, audiencia,
       vigencia con tolerancia de reloj; R5.3). Si la verificacion falla, NO se
       crea ninguna sesion y se responde ``401`` generico (R5.5; Property 4).
    2. Solo tras una verificacion exitosa, crea la session cookie con
       `create_session_cookie`, cuya expiracion se acota al rango permitido por
       el Admin SDK a partir de ``SESSION_COOKIE_MAX_AGE_SECONDS`` (R5.6). Si la
       creacion falla, tampoco se establece sesion y se responde ``401``
       generico (R5.4).
    3. En exito, entrega la cookie con atributos ``Secure``, ``HttpOnly``,
       ``SameSite=Strict``, ``Path=/`` y ``Max-Age`` acotado, y responde ``204``
       sin cuerpo: nunca se devuelve el ID token ni la session cookie en el
       cuerpo (R5.6; Property 5).

    Los secretos del Admin SDK permanecen exclusivamente server-side; ni el
    token, ni la cookie, ni la causa concreta del fallo se registran o se
    exponen al cliente (R5.2, R10.4). El fallo de esquema del cuerpo (token
    ausente, demasiado largo o claves inesperadas) lo traduce el manejador
    global de validacion a un ``422`` generico (R10.6, R10.7).

    :param request: solicitud HTTP, portadora del Correlation-ID de contexto.
    :param payload: cuerpo validado con el ID token a verificar.
    :return: respuesta ``204`` con la session cookie en ``Set-Cookie`` (exito) o
        ``401`` generica sin cookie (fallo de verificacion o de creacion).
    """
    correlation_id = _resolve_correlation_id(request)

    # Refuerzo de rate limiting de la creacion de sesion (R3.7, R4.10). Se
    # evalua antes de verificar el token para contener el abuso del endpoint sin
    # ejercitar el Admin SDK. Es refuerzo del bloqueo primario de Firebase/F5.
    rate_limited = _enforce_rate_limit(
        request, _RATE_LIMIT_SCOPE_SESSION, correlation_id
    )
    if rate_limited is not None:
        return rate_limited

    # Paso 1: verificar el token. Cualquier fallo (invalido/expirado o SDK no
    # inicializable) deniega sin crear sesion (Property 4). El token nunca se
    # registra: la verificacion ya registra solo el tipo de error internamente.
    # Se CAPTURAN los claims (antes se descartaban) para inspeccionar el
    # proveedor y, en el camino B, el correo corporativo (POCA-004).
    try:
        claims = verify_id_token(payload.id_token)
    except (InvalidIdTokenError, FirebaseAuthError):
        _logger.warning(
            "session_rejected_invalid_token",
            extra={"resolved_correlation_id": correlation_id},
        )
        return _auth_failure_response(correlation_id)

    # Paso 1b (camino B, POCA-004): validacion server-side de dominio corporativo.
    # SOLO cuando el proveedor es exactamente google.com, la autorizacion se ancla
    # en el claim `email` del token verificado (no en el `hd` del frontend). Si el
    # dominio del correo no esta en la allowlist por entorno (o el correo falta o
    # no es extraible, o la allowlist esta vacia), se deniega con el MISMO 401
    # generico, sin `Set-Cookie` ni motivo, antes de crear la cookie (defensa en
    # profundidad / BOLA; fail-safe deny). Los proveedores password/saml.<id> no
    # pasan por aqui y conservan su flujo. No se registra el correo ni el dominio:
    # solo el correlation_id y el evento (leakage prevention, R10.3/R10.4).
    if _is_google_provider(claims):
        email = claims.get(_EMAIL_CLAIM)
        if not is_allowed_corporate_email(email, get_corporate_allowed_domains()):
            _logger.warning(
                "session_rejected_domain_not_allowed",
                extra={"resolved_correlation_id": correlation_id},
            )
            return _auth_failure_response(correlation_id)

    # Paso 2: crear la session cookie solo tras verificar. La expiracion se
    # resuelve por configuracion y se acota al rango del SDK (R5.6).
    max_age_seconds = get_session_cookie_max_age_seconds()
    try:
        session_cookie = create_session_cookie(payload.id_token, max_age_seconds)
    except FirebaseAuthError:
        # Incluye `SessionCookieError`. No se establece sesion (R5.4).
        _logger.warning(
            "session_cookie_creation_rejected",
            extra={"resolved_correlation_id": correlation_id},
        )
        return _auth_failure_response(correlation_id)

    # Paso 3: entregar la cookie segura y responder 204 sin cuerpo ni token
    # (Property 5). El valor de la cookie nunca se registra.
    cookie_name = get_session_cookie_name()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.headers[CORRELATION_ID_HEADER] = correlation_id
    # Los atributos Secure/SameSite se resuelven por entorno (APP_ENV): en
    # produccion son `Secure` + `SameSite=Strict` (fail-safe por defecto); solo
    # en local se relajan a no-Secure + `SameSite=Lax` para el flujo HTTP del
    # dev server. `HttpOnly` y `Path=/` se mantienen siempre.
    response.set_cookie(
        key=cookie_name,
        value=session_cookie,
        max_age=max_age_seconds,
        path="/",
        secure=get_session_cookie_secure(),
        httponly=True,
        samesite=get_session_cookie_samesite(),
    )

    _logger.info(
        "session_created",
        extra={"resolved_correlation_id": correlation_id},
    )
    return response


def _clear_session_cookie(response: Response, cookie_name: str) -> None:
    """Elimina la session cookie fijando ``Set-Cookie`` con ``Max-Age=0``.

    Reemplaza la cookie por una vacia y ya expirada, conservando los mismos
    atributos de ambito y seguridad con que se creo (``Path=/``, ``Secure``,
    ``HttpOnly``, ``SameSite=Strict``), de modo que el navegador la descarte de
    inmediato. Es la parte determinista y siempre ejecutada del logout: no
    depende de que exista una sesion previa, por lo que contribuye al estado
    final unico "sin sesion" (R6.5; Property 9).

    :param response: respuesta HTTP sobre la que se fija la cabecera de borrado.
    :param cookie_name: nombre de la cookie de sesion a eliminar.
    """
    # Se conservan los mismos atributos de ambito y seguridad resueltos por
    # entorno con que se creo la cookie (Secure/SameSite via APP_ENV; HttpOnly
    # y Path=/ constantes), de modo que el borrado coincida con la cookie
    # original y el navegador la descarte de inmediato.
    response.set_cookie(
        key=cookie_name,
        value="",
        max_age=0,
        path="/",
        secure=get_session_cookie_secure(),
        httponly=True,
        samesite=get_session_cookie_samesite(),
    )


def _resolve_session_uid(session_cookie: str) -> str | None:
    """Resuelve el ``uid`` de una session cookie, o ``None`` si no es valida.

    Verifica la cookie de forma defensiva con ``verify_session_cookie`` del
    modulo de integracion con Firebase. Dado que esa funcion se materializa en
    otra tarea, se resuelve en tiempo de ejecucion con ``getattr``: si aun no
    esta disponible, se trata como "sin sesion resoluble" y se devuelve
    ``None``, de modo que el logout siga siendo idempotente y limpie la cookie
    igualmente (Property 9).

    Cualquier fallo de verificacion (cookie ausente, mal formada, expirada o
    revocada) se interpreta como ausencia de sesion valida y devuelve ``None``
    sin propagar la causa ni registrar el valor de la cookie (R6.3, R10.4).

    :param session_cookie: valor de la cookie de sesion leido de la solicitud.
    :return: el ``uid`` del usuario si la cookie es valida; ``None`` en caso
        contrario.
    """
    verify = getattr(firebase_auth, "verify_session_cookie", None)
    if verify is None:
        # La verificacion aun no esta disponible (otra tarea la provee). El
        # logout permanece idempotente: no se revoca, pero se limpia la cookie.
        return None

    try:
        claims = verify(session_cookie)
    except FirebaseAuthError:
        # Incluye `InvalidSessionCookieError` y fallos de inicializacion del
        # SDK. Sin sesion valida resoluble; nunca se registra la cookie.
        return None

    if not isinstance(claims, dict):
        return None

    uid = claims.get(_UID_CLAIM) or claims.get(_SUBJECT_CLAIM)
    return uid if isinstance(uid, str) and uid else None


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cierra la sesion de forma idempotente",
    responses={
        status.HTTP_204_NO_CONTENT: {
            "description": (
                "Sesion cerrada. La session cookie se elimina via Set-Cookie "
                "con Max-Age=0 (Path=/, Secure, HttpOnly, SameSite=Strict) y el "
                "cuerpo de la respuesta esta vacio. La operacion es idempotente: "
                "invocarla con o sin sesion valida produce el mismo estado final."
            )
        },
    },
)
def logout(request: Request) -> Response:
    """Cierra la sesion de forma idempotente (R6.5; Property 9).

    Lleva al sistema al mismo estado final "sin sesion" independientemente del
    numero de invocaciones y de que exista o no una sesion valida:

    1. Lee la session cookie por su nombre configurado. Si esta presente,
       resuelve el ``uid`` verificandola de forma defensiva; si es valida,
       revoca sus tokens de refresco con `revoke_session`, de modo que toda
       solicitud posterior con esa sesion sea rechazada como no autorizada
       (R6.5; Property 9). Si no hay cookie o no es valida, omite la revocacion.
    2. Elimina siempre la session cookie (``Set-Cookie`` con ``Max-Age=0``,
       ``Path=/``, ``Secure``, ``HttpOnly``, ``SameSite=Strict``), tanto si se
       revoco una sesion como si no.
    3. Responde ``204`` sin cuerpo.

    Un fallo al revocar los tokens no interrumpe el cierre: la cookie se limpia
    igualmente para preservar la idempotencia del estado del navegador. Ni el
    valor de la cookie ni el ``uid`` se registran o se exponen (R10.3, R10.4).

    :param request: solicitud HTTP, portadora de la cookie y del Correlation-ID.
    :return: respuesta ``204`` sin cuerpo, con la cookie de sesion eliminada.
    """
    correlation_id = _resolve_correlation_id(request)
    cookie_name = get_session_cookie_name()

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.headers[CORRELATION_ID_HEADER] = correlation_id

    session_cookie = request.cookies.get(cookie_name)
    revoked = False
    if session_cookie:
        uid = _resolve_session_uid(session_cookie)
        if uid is not None:
            try:
                revoke_session(uid)
                revoked = True
            except (SessionRevocationError, FirebaseAuthError):
                # La revocacion fallo, pero el logout no se interrumpe: la
                # cookie se limpia igual para mantener el estado idempotente.
                # El uid y la causa nunca se registran (R6.5, R10.4).
                _logger.warning(
                    "logout_revocation_failed",
                    extra={"resolved_correlation_id": correlation_id},
                )

    # Paso 2: siempre eliminar la cookie, haya o no sesion previa (Property 9).
    _clear_session_cookie(response, cookie_name)

    _logger.info(
        "logout_completed",
        extra={
            "resolved_correlation_id": correlation_id,
            "session_revoked": revoked,
        },
    )
    return response
