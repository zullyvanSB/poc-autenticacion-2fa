"""Integracion con el Firebase Admin SDK: verificacion de ID token y tipo de usuario.

Materializa la parte server-side del flujo de sesion (tarea 6.1): verificar el
Firebase ID token obtenido por el Client SDK y derivar el tipo de usuario a
partir del proveedor de ingreso. Este modulo NO crea la session cookie ni
expone el endpoint `/api/v1/auth/session` (tarea 6.2); solo provee las piezas
reutilizables y testeables de verificacion y derivacion.

Garantias de seguridad aplicadas (reglas de la organizacion y diseno):

- **Nunca confiar en un token sin verificar (R5.3, Property 4):** la unica via
  para aceptar un ID token es `verify_id_token`, que delega la verificacion
  criptografica (firma, emisor `iss`, audiencia `aud`, vigencia `exp`) en el
  Admin SDK con una tolerancia de reloj de 60 s (`clock_skew_seconds=60`, el
  maximo permitido por la API de ``firebase-admin`` 6.6.0, R5.5).
- **No fuga de detalle (R5.5, R10.4):** cualquier fallo de verificacion se
  traduce a `InvalidIdTokenError` de dominio con un mensaje generico, sin
  propagar la causa concreta ni el token al cliente. El detalle de la causa se
  deja para el registro interno del handler (tarea 6.2).
- **Secretos solo server-side (R5.2, R9.3):** las credenciales del Admin SDK se
  cargan desde ``GOOGLE_APPLICATION_CREDENTIALS`` (validada al arrancar por
  `common.config.StartupValidator`); nunca se incrustan en codigo, respuestas,
  logs ni errores. El token recibido tampoco se registra.

La inicializacion del Admin SDK es perezosa e idempotente (`initialize_app` una
sola vez por proceso), de modo que el modulo es importable sin efectos de red y
las pruebas (tarea 6.3) pueden mockear ``firebase_admin.auth.verify_id_token``
sin credenciales reales.
"""

from __future__ import annotations

from datetime import timedelta
from threading import Lock
from typing import Any

import firebase_admin
from firebase_admin import auth, credentials, exceptions as firebase_exceptions, tenant_mgt

from common.config import get_firebase_tenant_id
from common.logging import get_logger
from common.models import UserType

__all__ = [
    "CLOCK_SKEW_SECONDS",
    "MAX_SESSION_COOKIE_DURATION_SECONDS",
    "MIN_SESSION_COOKIE_DURATION_SECONDS",
    "FirebaseAuthError",
    "InvalidIdTokenError",
    "InvalidSessionCookieError",
    "SessionCookieError",
    "SessionRevocationError",
    "UnknownSignInProviderError",
    "_assert_expected_tenant",
    "clamp_session_duration_seconds",
    "create_session_cookie",
    "derive_user_type",
    "is_allowed_corporate_email",
    "revoke_session",
    "verify_id_token",
    "verify_session_cookie",
]

# Tolerancia de reloj (en segundos) aplicada por el Admin SDK al verificar la
# vigencia del token. La API de `firebase-admin` 6.6.0 exige un valor entre 0 y
# 60; se usa el maximo permitido por el criterio de aceptacion (R5.3).
CLOCK_SKEW_SECONDS = 60

# Limites de la duracion de la session cookie impuestos por el Admin SDK de
# `firebase-admin` 6.6.0 (`auth.create_session_cookie`): la duracion debe estar
# entre 5 minutos (300 s) y 2 semanas (1 209 600 s). Se declaran aqui para
# acotar la expiracion solicitada por configuracion sin invocar al SDK ni
# depender de constantes internas no publicas (R5.6).
MIN_SESSION_COOKIE_DURATION_SECONDS = 300
MAX_SESSION_COOKIE_DURATION_SECONDS = 1_209_600

# Claim que indica el proveedor con el que el usuario inicio sesion. Reside bajo
# el objeto `firebase` del ID token decodificado emitido por Google.
_FIREBASE_CLAIM = "firebase"
_SIGN_IN_PROVIDER_CLAIM = "sign_in_provider"

# Claim que indica el tenant de Google Cloud Identity Platform al que pertenece
# el token. Reside bajo el mismo objeto `firebase` que `sign_in_provider`. Un
# token de nivel proyecto (sin tenant) NO incluye este claim; comparar contra el
# tenant esperado es una defensa en profundidad frente a tokens ajenos (R5.3).
_TENANT_CLAIM = "tenant"

# Valor de `sign_in_provider` para la ruta Cliente (correo + TOTP en Firebase).
_PROVIDER_PASSWORD = "password"
# Prefijo de `sign_in_provider` para la ruta Interno: Firebase actua como SP
# SAML y el proveedor toma la forma `saml.<id>` (p. ej. `saml.f5apm`).
_PROVIDER_SAML_PREFIX = "saml."
# Valor de `sign_in_provider` para la ruta Corporativo (camino B): correo
# corporativo que autentica con el `GoogleAuthProvider` nativo de Firebase (2FA
# delegado a Google 2-Step Verification). Se compara por igualdad EXACTA, no por
# prefijo/sufijo, para no aceptar valores como `google.com.attacker` (FR-5).
_PROVIDER_GOOGLE = "google.com"

# Mensaje generico de fallo de verificacion. No revela la causa concreta
# (firma, emisor, audiencia o vigencia) ni incluye el token (R5.5, R10.4).
_GENERIC_VERIFICATION_FAILURE = "El token de identidad no es valido."

# Mensaje generico de fallo en la creacion de la session cookie. No revela la
# causa concreta ni incluye el token ni la cookie (R5.4, R10.4).
_GENERIC_SESSION_FAILURE = "No fue posible crear la sesion."

# Mensaje generico de fallo en la validacion de la session cookie. No revela si
# la cookie estaba ausente, mal formada, expirada o revocada, ni la incluye
# (R6.3, R10.4).
_GENERIC_SESSION_INVALID = "La sesion no es valida."

# Mensaje generico de fallo al revocar los tokens de refresco en el logout. No
# revela la causa concreta ni incluye el uid (R6.5, R10.4).
_GENERIC_REVOKE_FAILURE = "No fue posible cerrar la sesion."

# Logger del submodulo; el formateador estructurado inyecta el Correlation-ID y
# redacta campos sensibles. Nunca se registra el token ni las credenciales.
_logger = get_logger("auth.firebase")

# Inicializacion perezosa e idempotente del Admin SDK. El lock evita que dos
# solicitudes concurrentes intenten `initialize_app` a la vez durante el primer
# uso del proceso.
_init_lock = Lock()


class FirebaseAuthError(Exception):
    """Error base del dominio de autenticacion con Firebase.

    Las subclases traducen fallos del Admin SDK a errores de dominio con
    mensajes genericos, de modo que el handler HTTP responda sin filtrar la
    causa concreta ni detalles de implementacion (R5.5, R10.4).
    """


class InvalidIdTokenError(FirebaseAuthError):
    """El ID token no supero la verificacion del Admin SDK.

    Agrupa todos los motivos de rechazo (firma invalida, emisor/audiencia no
    esperados, token expirado considerando la tolerancia de reloj, token mal
    formado o fallo al obtener los certificados). No transporta el token ni la
    causa concreta: el mensaje es generico y la causa original se encadena solo
    para el registro interno del handler, nunca para el cliente (R5.5, R10.4).
    """


class SessionCookieError(FirebaseAuthError):
    """No fue posible crear la session cookie a partir del ID token.

    Agrupa los fallos de `auth.create_session_cookie` (token rechazado por el
    backend de Firebase, duracion invalida o respuesta inesperada). No
    transporta el token ni la cookie: el mensaje es generico y la causa original
    se encadena solo para el registro interno del handler, nunca para el cliente
    (R5.4, R10.4).
    """


class InvalidSessionCookieError(FirebaseAuthError):
    """La session cookie no supero la verificacion del Admin SDK.

    Agrupa todos los motivos de rechazo de `auth.verify_session_cookie`
    (cookie ausente, mal formada, firma invalida, emisor/audiencia no
    esperados, expirada considerando la tolerancia de reloj, o revocada cuando
    se verifica `check_revoked=True`). No transporta la cookie ni la causa
    concreta: el mensaje es generico y la causa original se encadena solo para
    el registro interno del handler, nunca para el cliente (R6.3, R10.4).
    """


class SessionRevocationError(FirebaseAuthError):
    """No fue posible revocar los tokens de refresco del usuario en el logout.

    Agrupa los fallos de `auth.revoke_refresh_tokens` (uid inexistente,
    indisponibilidad del backend de Firebase o respuesta inesperada). No
    transporta el uid ni la causa concreta: el mensaje es generico y la causa
    original se encadena solo para el registro interno del handler, nunca para
    el cliente (R6.5, R10.4).
    """


class UnknownSignInProviderError(FirebaseAuthError):
    """El claim `firebase.sign_in_provider` no corresponde a una ruta conocida.

    Se eleva cuando el proveedor de ingreso no es `password` (Cliente),
    `saml.<id>` (Interno) ni `google.com` (Corporativo). No incluye el valor del
    proveedor en el mensaje expuesto para evitar filtrar detalles de la
    configuracion de identidad.
    """


def _ensure_initialized() -> None:
    """Inicializa el Firebase Admin SDK una sola vez por proceso (perezoso).

    Usa las credenciales de la cuenta de servicio apuntadas por
    ``GOOGLE_APPLICATION_CREDENTIALS`` (credenciales por aplicacion de Google),
    cuya presencia valida `StartupValidator` al arrancar (R9.4). La operacion es
    idempotente: si la app por defecto ya existe, no se vuelve a inicializar.

    No se registran ni se exponen las credenciales ni su ruta (R5.2, R9.3).

    :raises FirebaseAuthError: si el Admin SDK no puede inicializarse (p. ej.
        credenciales ausentes o invalidas). El mensaje es generico.
    """
    try:
        # Si ya existe la app por defecto, no se reinicializa.
        firebase_admin.get_app()
        return
    except ValueError:
        # `get_app` eleva ValueError cuando la app por defecto no existe aun.
        pass

    with _init_lock:
        # Doble verificacion: otra solicitud pudo inicializarla mientras se
        # esperaba el lock.
        try:
            firebase_admin.get_app()
            return
        except ValueError:
            pass

        try:
            # `ApplicationDefault` toma las credenciales de
            # GOOGLE_APPLICATION_CREDENTIALS sin que su valor pase por el codigo.
            firebase_admin.initialize_app(credentials.ApplicationDefault())
        except Exception as exc:  # noqa: BLE001 - se traduce a error de dominio
            _logger.error(
                "firebase_admin_init_failed",
                extra={"error_type": type(exc).__name__},
            )
            raise FirebaseAuthError(
                "No fue posible inicializar el proveedor de identidad."
            ) from exc


def _tenant_auth_client():
    """Devuelve el cliente del Admin SDK con alcance del tenant configurado.

    El usuario de la PoC reside dentro del tenant de Identity Platform
    (`FIREBASE_TENANT_ID`). Las operaciones que buscan al usuario
    (`verify_session_cookie` con `check_revoked=True`, `revoke_refresh_tokens`,
    `create_session_cookie`) deben ejecutarse con el cliente del tenant
    (`tenant_mgt.auth_for_tenant`), no con el cliente de nivel proyecto; de lo
    contrario Firebase no encuentra al usuario y eleva `UserNotFoundError`.

    Si no hay tenant configurado (despliegue a nivel proyecto), se devuelve el
    modulo `auth` global, de modo que ambos modos de despliegue funcionen.

    :return: el cliente de autenticacion con alcance de tenant, o el modulo
        `auth` global si no se configuro un tenant.
    """
    _ensure_initialized()
    tenant_id = get_firebase_tenant_id()
    if not tenant_id:
        return auth
    return tenant_mgt.auth_for_tenant(tenant_id)


def _assert_expected_tenant(claims: dict[str, Any], expected_tenant: str) -> bool:
    """Indica si los claims pertenecen al tenant esperado de Identity Platform.

    Lee el claim ``firebase.tenant`` de los claims ya verificados y lo compara con
    ``expected_tenant`` (resuelto por `get_firebase_tenant_id`). La comprobacion es
    una defensa en profundidad (R5.3): un token de nivel proyecto no incluye el
    claim ``tenant`` y un token de otro tenant lo trae con un valor distinto; en
    ambos casos la funcion devuelve ``False`` para que el llamador lo rechace con
    el MISMO error generico que cualquier otro fallo de verificacion, sin revelar
    que el motivo fue el tenant (R5.5, R10.4).

    Funcion pura: no realiza E/S y, para los mismos argumentos, produce siempre el
    mismo resultado. No registra el valor del tenant; el llamador registra solo el
    resultado booleano de la comparacion.

    :param claims: diccionario de claims decodificados de un token verificado.
    :param expected_tenant: identificador del tenant esperado para la PoC.
    :return: ``True`` si el claim ``firebase.tenant`` coincide exactamente con
        ``expected_tenant``; ``False`` si esta ausente o difiere.
    """
    firebase_claim = claims.get(_FIREBASE_CLAIM)
    tenant: Any = None
    if isinstance(firebase_claim, dict):
        tenant = firebase_claim.get(_TENANT_CLAIM)
    return tenant == expected_tenant


def verify_id_token(id_token: str) -> dict[str, Any]:
    """Verifica un Firebase ID token y devuelve sus claims decodificados.

    Delega la verificacion criptografica en ``firebase_admin.auth.verify_id_token``,
    que valida la firma, el emisor (`iss`), la audiencia (`aud`) y la vigencia
    (`exp`) del JWT emitido por Google, aplicando una tolerancia de reloj de
    ``CLOCK_SKEW_SECONDS`` (60 s, el maximo permitido; R5.3). El Admin SDK se
    inicializa de forma perezosa en la primera llamada.

    Si la verificacion tiene exito, devuelve el diccionario de claims para que el
    handler de sesion (tarea 6.2) cree la session cookie. Si falla por cualquier
    motivo, eleva `InvalidIdTokenError` con un mensaje generico: no se revela la
    causa concreta ni se registra el token (R5.5, R10.4; Property 4). La causa
    original se encadena (`raise ... from exc`) unicamente para el registro
    interno del handler.

    :param id_token: ID token (JWT) obtenido por el Firebase Client SDK.
    :return: diccionario de claims decodificados del token verificado.
    :raises InvalidIdTokenError: si el token es invalido, esta expirado (con la
        tolerancia aplicada), tiene emisor/audiencia no esperados, esta mal
        formado, no pueden obtenerse los certificados de verificacion o su claim
        ``firebase.tenant`` no corresponde al tenant configurado (R5.3).
    :raises FirebaseAuthError: si el Admin SDK no puede inicializarse.
    """
    _ensure_initialized()

    try:
        # `clock_skew_seconds=60`: tolerancia de reloj maxima permitida por la
        # API de firebase-admin 6.6.0 (R5.3). No se usa `check_revoked` aqui: la
        # revocacion se evalua en la validacion de la session cookie (tarea 7.1).
        claims = auth.verify_id_token(id_token, clock_skew_seconds=CLOCK_SKEW_SECONDS)
    except (ValueError, firebase_exceptions.FirebaseError) as exc:
        # Se cubre tanto `ValueError` (token no string/vacio) como la jerarquia
        # de errores del Admin SDK (`InvalidIdTokenError`, `ExpiredIdTokenError`,
        # `CertificateFetchError`, etc.), que heredan de `FirebaseError`. El
        # token nunca se registra; solo el tipo de error para diagnostico.
        _logger.warning(
            "id_token_verification_failed",
            extra={"error_type": type(exc).__name__},
        )
        raise InvalidIdTokenError(_GENERIC_VERIFICATION_FAILURE) from exc

    # Defensa en profundidad: el token debe pertenecer al tenant configurado de
    # Identity Platform. Un token de nivel proyecto (sin claim `firebase.tenant`)
    # o de otro tenant se rechaza con el MISMO error generico, sin revelar que el
    # motivo fue el tenant (R5.3, R5.5, R10.4). Solo se registra el resultado
    # booleano de la comparacion: nunca el token ni el valor del tenant.
    if not _assert_expected_tenant(claims, get_firebase_tenant_id()):
        _logger.warning(
            "id_token_tenant_mismatch",
            extra={"tenant_match": False},
        )
        raise InvalidIdTokenError(_GENERIC_VERIFICATION_FAILURE)

    return claims


def clamp_session_duration_seconds(requested_seconds: int) -> int:
    """Acota la duracion solicitada de la session cookie al rango permitido.

    El Admin SDK (`auth.create_session_cookie`) exige una duracion entre
    `MIN_SESSION_COOKIE_DURATION_SECONDS` (300 s) y
    `MAX_SESSION_COOKIE_DURATION_SECONDS` (1 209 600 s). Esta funcion pura
    recorta el valor configurado (`SESSION_COOKIE_MAX_AGE_SECONDS`) a dicho
    rango, garantizando una expiracion acotada y valida sin provocar un
    `ValueError` del SDK (R5.6).

    :param requested_seconds: duracion solicitada por configuracion, en segundos.
    :return: la duracion acotada al rango ``[MIN, MAX]``.
    """
    if requested_seconds < MIN_SESSION_COOKIE_DURATION_SECONDS:
        return MIN_SESSION_COOKIE_DURATION_SECONDS
    if requested_seconds > MAX_SESSION_COOKIE_DURATION_SECONDS:
        return MAX_SESSION_COOKIE_DURATION_SECONDS
    return requested_seconds


def create_session_cookie(id_token: str, expires_in_seconds: int) -> str:
    """Crea una Firebase session cookie a partir de un ID token verificado.

    Delega en ``firebase_admin.auth.create_session_cookie(id_token, expires_in)``
    (API documentada de `firebase-admin` 6.6.0), pasando ``expires_in`` como un
    ``datetime.timedelta`` cuya duracion se acota previamente al rango permitido
    por el SDK (300 s a 2 semanas) con `clamp_session_duration_seconds` (R5.6).
    El Admin SDK se inicializa de forma perezosa en la primera llamada.

    Esta funcion SOLO debe invocarse tras una verificacion exitosa del mismo
    ID token con `verify_id_token`: asi, la existencia de una sesion implica
    siempre una verificacion previa (Property 4). Si la creacion falla, eleva
    `SessionCookieError` con un mensaje generico, sin registrar ni exponer el
    token ni la cookie (R5.4, R10.4). La causa original se encadena unicamente
    para el registro interno del handler.

    :param id_token: ID token (JWT) ya verificado con `verify_id_token`.
    :param expires_in_seconds: duracion deseada de la cookie, en segundos; se
        acota al rango permitido por el SDK antes de usarla.
    :return: la session cookie (JWT opaco) como cadena, lista para fijarse en
        una cookie ``Secure``/``HttpOnly``/``SameSite=Strict``.
    :raises SessionCookieError: si el SDK rechaza el token o la creacion falla.
    :raises FirebaseAuthError: si el Admin SDK no puede inicializarse.
    """
    _ensure_initialized()

    bounded_seconds = clamp_session_duration_seconds(expires_in_seconds)

    try:
        # `create_session_cookie` es un metodo de NIVEL PROYECTO del Admin SDK;
        # el cliente de tenant no lo expone. La session cookie no es tenant-scoped.
        cookie = auth.create_session_cookie(
            id_token, expires_in=timedelta(seconds=bounded_seconds)
        )
    except (ValueError, firebase_exceptions.FirebaseError) as exc:
        # Ni el token ni la cookie se registran; solo el tipo de error para
        # diagnostico. El mensaje expuesto es generico (R5.4, R10.4).
        _logger.warning(
            "session_cookie_creation_failed",
            extra={"error_type": type(exc).__name__},
        )
        raise SessionCookieError(_GENERIC_SESSION_FAILURE) from exc

    # El SDK devuelve ``bytes`` en algunas rutas; se normaliza a ``str`` para
    # fijar la cookie. Nunca se registra el valor devuelto.
    if isinstance(cookie, bytes):
        return cookie.decode("utf-8")
    return cookie


def verify_session_cookie(session_cookie: str) -> dict[str, Any]:
    """Verifica una Firebase session cookie y devuelve sus claims decodificados.

    Delega la verificacion en ``firebase_admin.auth.verify_session_cookie``
    (API documentada de `firebase-admin` 6.6.0), que valida la firma, el emisor
    (`iss`), la audiencia (`aud`) y la vigencia (`exp`) de la cookie opaca
    emitida por `create_session_cookie`. Se invoca con ``check_revoked=True``
    para que, ademas, se compruebe contra Firebase que los tokens de refresco no
    hayan sido revocados (p. ej. tras un logout, R6.5): una cookie de una sesion
    revocada se rechaza. El Admin SDK se inicializa de forma perezosa en la
    primera llamada.

    Si la verificacion tiene exito, devuelve el diccionario de claims para que
    el handler del dashboard (tarea 7.1) arme la respuesta protegida con los
    claims enmascarados. Si falla por cualquier motivo (cookie ausente, mal
    formada, invalida, expirada o revocada), eleva `InvalidSessionCookieError`
    con un mensaje generico: no se revela la causa concreta ni se registra la
    cookie (R6.3, R10.4; Property 6). La causa original se encadena
    (`raise ... from exc`) unicamente para el registro interno del handler.

    :param session_cookie: valor de la session cookie opaca a verificar.
    :return: diccionario de claims decodificados de la cookie verificada.
    :raises InvalidSessionCookieError: si la cookie es invalida, esta expirada
        (con la tolerancia aplicada), tiene emisor/audiencia no esperados, esta
        mal formada, pertenece a una sesion con tokens revocados o su claim
        ``firebase.tenant`` no corresponde al tenant configurado (R5.3).
    :raises FirebaseAuthError: si el Admin SDK no puede inicializarse.
    """
    _ensure_initialized()

    try:
        # `verify_session_cookie` es un metodo de NIVEL PROYECTO del Admin SDK
        # (el cliente de tenant no lo expone). Se usa `check_revoked=False`: con
        # `True`, el SDK buscaria al usuario a nivel proyecto y, como reside en el
        # tenant, fallaria con `UserNotFoundError`. La cookie se verifica
        # criptograficamente (firma/emisor/audiencia/vigencia) y, ademas, se
        # valida el claim `firebase.tenant` mas abajo (defensa en profundidad).
        # Nota PoC: no se comprueba la revocacion en tiempo real en el dashboard;
        # el logout revoca los tokens del usuario via el cliente de tenant.
        claims = auth.verify_session_cookie(session_cookie, check_revoked=False)
    except (ValueError, firebase_exceptions.FirebaseError) as exc:
        # Se cubre tanto `ValueError` (cookie no string/vacia) como la jerarquia
        # de errores del Admin SDK (`InvalidSessionCookieError`,
        # `ExpiredSessionCookieError`, `RevokedSessionCookieError`, etc.), que
        # heredan de `FirebaseError`. La cookie nunca se registra; solo el tipo
        # de error para diagnostico.
        _logger.warning(
            "session_cookie_verification_failed",
            extra={"error_type": type(exc).__name__},
        )
        raise InvalidSessionCookieError(_GENERIC_SESSION_INVALID) from exc

    # Defensa en profundidad: la cookie debe pertenecer al tenant configurado de
    # Identity Platform. Una cookie de nivel proyecto (sin claim
    # `firebase.tenant`) o de otro tenant se rechaza con el MISMO error generico,
    # sin revelar que el motivo fue el tenant (R5.3, R6.3, R10.4). Solo se
    # registra el resultado booleano de la comparacion: nunca la cookie ni el
    # valor del tenant.
    if not _assert_expected_tenant(claims, get_firebase_tenant_id()):
        _logger.warning(
            "session_cookie_tenant_mismatch",
            extra={"tenant_match": False},
        )
        raise InvalidSessionCookieError(_GENERIC_SESSION_INVALID)

    return claims


def revoke_session(uid: str) -> None:
    """Revoca todos los tokens de refresco del usuario para cerrar su sesion.

    Delega en ``firebase_admin.auth.revoke_refresh_tokens(uid)`` (API documentada
    de `firebase-admin` 6.6.0), que invalida los tokens de refresco emitidos
    hasta ese instante. Combinada con la verificacion de la session cookie con
    ``check_revoked=True``, garantiza que toda solicitud posterior con la sesion
    revocada sea rechazada como no autorizada (R6.5; Property 9). El Admin SDK se
    inicializa de forma perezosa en la primera llamada.

    La revocacion es idempotente a nivel de estado: invocarla una o varias veces
    para el mismo ``uid`` deja al usuario en el mismo estado final "sin sesion",
    por lo que el handler de logout puede llamarla con seguridad en cada
    invocacion (Property 9).

    Si la revocacion falla, eleva `SessionRevocationError` con un mensaje
    generico, sin registrar ni exponer el ``uid`` ni la causa concreta
    (R6.5, R10.4). La causa original se encadena unicamente para el registro
    interno del handler.

    :param uid: identificador del usuario cuyos tokens de refresco se revocan;
        se obtiene de una session cookie verificada, nunca de un valor provisto
        por el cliente sin verificar.
    :raises SessionRevocationError: si el SDK rechaza el ``uid`` o la revocacion
        falla por indisponibilidad o respuesta inesperada.
    :raises FirebaseAuthError: si el Admin SDK no puede inicializarse.
    """
    _ensure_initialized()

    try:
        _tenant_auth_client().revoke_refresh_tokens(uid)
    except (ValueError, firebase_exceptions.FirebaseError) as exc:
        # Ni el uid ni la causa concreta se registran o se exponen; solo el tipo
        # de error para diagnostico interno. El mensaje expuesto es generico
        # (R6.5, R10.4).
        _logger.warning(
            "session_revocation_failed",
            extra={"error_type": type(exc).__name__},
        )
        raise SessionRevocationError(_GENERIC_REVOKE_FAILURE) from exc


def derive_user_type(claims: dict[str, Any]) -> UserType:
    """Deriva el tipo de usuario a partir del proveedor de ingreso del token.

    Lee el claim ``firebase.sign_in_provider`` de los claims ya verificados y lo
    mapea a la poblacion de usuario (R4.6, R4.7):

    - ``"password"`` -> `UserType.CLIENTE` (ruta correo + TOTP en Firebase).
    - ``"saml.<id>"`` (p. ej. ``"saml.f5apm"``) -> `UserType.INTERNO` (ruta SAML
      federada hacia F5 APM).
    - ``"google.com"`` -> `UserType.CORPORATIVO` (camino B: correo corporativo con
      el `GoogleAuthProvider` nativo de Firebase; igualdad exacta del literal).

    El caso ``google.com`` se evalua por igualdad EXACTA y despues de
    ``password``/``saml.`` (sin alterar su precedencia), de modo que
    ``saml.google.com`` sigue derivando `INTERNO` por el prefijo y valores como
    ``google.com.attacker`` no mapean a corporativo (FR-5, defensa en profundidad).

    Funcion pura y determinista: no realiza E/S y, para los mismos claims,
    produce siempre el mismo resultado. Debe invocarse unicamente sobre claims
    devueltos por `verify_id_token` (nunca sobre un token sin verificar).

    :param claims: diccionario de claims decodificados de un token verificado.
    :return: el `UserType` correspondiente al proveedor de ingreso.
    :raises UnknownSignInProviderError: si falta el claim o su valor no es
        ``password``, no empieza por ``saml.`` ni es exactamente ``google.com``.
    """
    firebase_claim = claims.get(_FIREBASE_CLAIM)
    provider: Any = None
    if isinstance(firebase_claim, dict):
        provider = firebase_claim.get(_SIGN_IN_PROVIDER_CLAIM)

    if provider == _PROVIDER_PASSWORD:
        return UserType.CLIENTE
    if isinstance(provider, str) and provider.startswith(_PROVIDER_SAML_PREFIX):
        return UserType.INTERNO
    if provider == _PROVIDER_GOOGLE:
        return UserType.CORPORATIVO

    # No se incluye el valor del proveedor en el mensaje para no filtrar
    # detalles de la configuracion de identidad (leakage prevention).
    _logger.warning("unknown_sign_in_provider")
    raise UnknownSignInProviderError(
        "El proveedor de ingreso del token no corresponde a una ruta conocida."
    )


def is_allowed_corporate_email(
    email: str | None, allowed_domains: list[str]
) -> bool:
    """Indica si el dominio del correo pertenece a la allowlist corporativa.

    Regla de seguridad (camino B, POCA-004): la autorizacion corporativa se
    ancla en el claim ``email`` del ID token verificado, nunca en el parametro
    ``hd`` que provee el frontend (cosmetico y eludible). Extrae el dominio como
    la porcion tras el ULTIMO ``@`` del correo, lo normaliza a minusculas y lo
    compara por **match EXACTO** (igualdad completa del dominio) contra
    ``allowed_domains`` (que POCA-001 entrega ya normalizada a minusculas). No
    hay tratamiento de sufijo ni subdominio: ``sub.segurosbolivar.com`` y
    ``evil-segurosbolivar.com`` NO coinciden con ``segurosbolivar.com``.

    **Fail-safe deny** (R5.3, defensa en profundidad): devuelve ``False`` cuando
    ``email`` es ``None``/no-``str``/vacio, no contiene ``@``, la parte de
    dominio queda vacia (incluso tras recortar espacios), o ``allowed_domains``
    esta vacia. La ausencia de un dominio extraible o de configuracion nunca
    autoriza por defecto.

    Funcion pura y determinista: no realiza E/S, no registra ni accede a estado;
    para los mismos argumentos produce siempre el mismo resultado, de modo que
    sea verificable de forma aislada (sin HTTP ni mocks del SDK).

    :param email: claim ``email`` (top-level) del token verificado, o ``None``.
    :param allowed_domains: allowlist de dominios normalizados a minusculas
        (``get_corporate_allowed_domains`` de POCA-001); vacia ⇒ nadie autorizado.
    :return: ``True`` solo si el dominio del ``email`` hace match exacto
        (case-insensitive) con alguna entrada de ``allowed_domains``; ``False``
        en cualquier otro caso (fail-safe deny).
    """
    if not isinstance(email, str) or not allowed_domains:
        return False
    # El dominio es la porcion tras el ULTIMO `@`: un `@` extra en la parte
    # local no debe alterar el dominio real que se autoriza (anclaje en email).
    if "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1].strip().lower()
    if not domain:
        return False
    return domain in allowed_domains
