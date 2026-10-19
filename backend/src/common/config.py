"""Validacion de configuracion por variables de entorno al arrancar.

El Backend_FastAPI obtiene la totalidad de su configuracion y secretos
exclusivamente de variables de entorno, sin valores embebidos en el codigo ni
en la imagen, de modo que el mismo artefacto opere en local y en GCP sin
cambios de codigo (R9.3). Al iniciar, `StartupValidator` verifica que todas las
variables requeridas esten presentes y no vacias; si falta alguna, aborta el
arranque indicando exactamente los nombres faltantes y ningun valor de secreto
(R9.4; regla de seguridad: leakage prevention).

Se usa una lectura directa de `os.environ` (sin dependencias adicionales) para
mantener el validador puro y facilmente verificable por property-based testing
(tarea 2.7).
"""

from __future__ import annotations

import os
from collections.abc import Mapping

# Nombre por defecto de la cookie de sesion si la variable de entorno no define
# uno valido. La cookie es opaca (session cookie de Firebase); el nombre no es
# un secreto (R5.6).
_DEFAULT_SESSION_COOKIE_NAME = "session"

# Entorno de ejecucion por defecto cuando `APP_ENV` esta ausente o vacio. El
# valor por defecto es "production" (fail-safe a seguro): cualquier valor que no
# sea exactamente "local" se normaliza a "production", de modo que una
# configuracion ausente o erronea nunca relaje los atributos de seguridad.
_DEFAULT_APP_ENV = "production"
_LOCAL_APP_ENV = "local"

# Origenes CORS por defecto para desarrollo local cuando `CORS_ALLOWED_ORIGINS`
# esta ausente o vacio y el entorno es local. Permiten que el dev server de
# Angular (http://localhost:4200 / http://127.0.0.1:4200) consuma el backend.
# En produccion el valor por defecto es una lista vacia (sin CORS).
_DEFAULT_LOCAL_CORS_ORIGINS = (
    "http://localhost:4200",
    "http://127.0.0.1:4200",
)

# Duracion por defecto (en segundos) de la session cookie cuando el valor
# configurado esta ausente o no es un entero positivo. Equivale a 1 hora, dentro
# del rango permitido por el Admin SDK (300 s a 2 semanas); la creacion de la
# cookie vuelve a acotar el valor por seguridad (R5.6).
_DEFAULT_SESSION_COOKIE_MAX_AGE_SECONDS = 3600

# Numero maximo de solicitudes permitidas por ventana para el refuerzo de rate
# limiting de los endpoints del backend (R3.7, R4.10). Valor por defecto cuando
# `RATE_LIMIT_MAX_REQUESTS` esta ausente, vacio o no es un entero positivo.
# Es un refuerzo de los bloqueos primarios de Firebase y F5, por lo que un valor
# holgado basta para contener abuso del endpoint sin interferir con el flujo.
_DEFAULT_RATE_LIMIT_MAX_REQUESTS = 10

# Duracion (en segundos) de la ventana fija de rate limiting. Valor por defecto
# cuando `RATE_LIMIT_WINDOW_SECONDS` esta ausente, vacio o no es un entero
# positivo. Alineado con la ventana de bloqueo de referencia de Firebase (300 s,
# R3.7) para que el refuerzo acompane al bloqueo primario del proveedor.
_DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 300

# Variables de entorno requeridas por el Backend_FastAPI. Cada entrada es el
# NOMBRE de la variable; su valor nunca se registra ni se expone. Derivadas de
# las necesidades de configuracion del diseno:
#   - credenciales del Firebase Admin SDK (GOOGLE_APPLICATION_CREDENTIALS),
#   - identificacion del proyecto Firebase/Identity Platform,
#   - identificador del tenant de Identity Platform al que pertenece la PoC
#     (defensa en profundidad: se rechazan tokens de otro tenant o de nivel
#     proyecto, R5.3),
#   - parametros de la session cookie (nombre y expiracion acotada, R5.6),
#   - identificador del proveedor SAML federado hacia F5 APM (R4),
#   - allowlist de URLs de proveedores de identidad (HTTPS, R10.5).
REQUIRED_ENV_VARS: tuple[str, ...] = (
    "GOOGLE_APPLICATION_CREDENTIALS",
    "FIREBASE_PROJECT_ID",
    "FIREBASE_TENANT_ID",
    "SESSION_COOKIE_NAME",
    "SESSION_COOKIE_MAX_AGE_SECONDS",
    "SAML_PROVIDER_ID",
    "ALLOWED_PROVIDER_URLS",
)


class MissingEnvironmentVariablesError(RuntimeError):
    """Error de arranque: faltan variables de entorno requeridas.

    El mensaje contiene unicamente los NOMBRES de las variables ausentes o
    vacias, nunca sus valores, para no filtrar secretos (R9.4). Se eleva como
    excepcion para abortar el arranque antes de que la aplicacion sirva
    trafico.
    """

    def __init__(self, missing: tuple[str, ...]) -> None:
        """Construye el error con la lista ordenada de nombres faltantes.

        :param missing: nombres de las variables de entorno ausentes o vacias.
        """
        self.missing: tuple[str, ...] = missing
        names = ", ".join(missing)
        super().__init__(
            "Faltan variables de entorno requeridas para el arranque: "
            f"{names}. Defina cada variable con un valor no vacio."
        )


def find_missing_env_vars(
    env: Mapping[str, str],
    required: tuple[str, ...] = REQUIRED_ENV_VARS,
) -> tuple[str, ...]:
    """Devuelve los nombres de las variables requeridas ausentes o vacias.

    Una variable se considera faltante si no esta presente en `env` o si su
    valor, tras recortar espacios en blanco, queda vacio. El orden de salida
    respeta el orden declarado en `required` para una salida determinista. No
    se inspeccionan ni se devuelven los valores (R9.4).

    :param env: mapeo de variables de entorno a evaluar (p. ej. ``os.environ``).
    :param required: nombres de las variables requeridas.
    :return: tupla con los nombres faltantes, en el orden de ``required``.
    """
    return tuple(name for name in required if not env.get(name, "").strip())


def get_firebase_tenant_id(env: Mapping[str, str] | None = None) -> str:
    """Devuelve el identificador del tenant de Identity Platform de la PoC (R5.3).

    Lee ``FIREBASE_TENANT_ID`` de las variables de entorno; su presencia la valida
    `StartupValidator` al arrancar. El backend compara este valor con el claim
    ``firebase.tenant`` de los ID token y session cookies verificados, de modo que
    un token de nivel proyecto o de otro tenant se rechace como defensa en
    profundidad. El identificador del tenant no es un secreto, por lo que puede
    devolverse; aun asi, nunca se registra junto a tokens para no facilitar
    correlaciones (leakage prevention).

    :param env: mapeo de variables de entorno; por defecto ``os.environ``.
    :return: el identificador del tenant configurado, recortado de espacios.
    """
    source: Mapping[str, str] = os.environ if env is None else env
    return source.get("FIREBASE_TENANT_ID", "").strip()


def get_app_env(env: Mapping[str, str] | None = None) -> str:
    """Devuelve el entorno de ejecucion normalizado: ``"local"`` o ``"production"``.

    Lee la variable opcional ``APP_ENV`` de las variables de entorno y la
    normaliza a minusculas tras recortar espacios. Cualquier valor distinto de
    ``"local"`` (incluyendo ausente, vacio o desconocido) se interpreta como
    ``"production"``, un comportamiento fail-safe hacia la configuracion segura:
    una configuracion ausente o erronea nunca relaja la seguridad. ``APP_ENV``
    no es un secreto y por eso puede derivarse y devolverse.

    :param env: mapeo de variables de entorno; por defecto ``os.environ``.
    :return: ``"local"`` si ``APP_ENV`` es exactamente "local"; ``"production"``
        en cualquier otro caso.
    """
    source: Mapping[str, str] = os.environ if env is None else env
    raw = source.get("APP_ENV", "").strip().lower()
    return _LOCAL_APP_ENV if raw == _LOCAL_APP_ENV else _DEFAULT_APP_ENV


def is_local_env(env: Mapping[str, str] | None = None) -> bool:
    """Indica si el entorno de ejecucion es el de desarrollo local.

    :param env: mapeo de variables de entorno; por defecto ``os.environ``.
    :return: ``True`` solo cuando ``APP_ENV`` es exactamente "local".
    """
    return get_app_env(env) == _LOCAL_APP_ENV


def get_cors_allowed_origins(env: Mapping[str, str] | None = None) -> list[str]:
    """Devuelve la lista de origenes permitidos para CORS.

    Lee la variable opcional ``CORS_ALLOWED_ORIGINS`` (lista separada por comas)
    de las variables de entorno, recorta cada entrada y descarta las vacias. Si
    la variable esta ausente o vacia tras el filtrado:

    - en entorno local devuelve los origenes por defecto del dev server de
      Angular (``http://localhost:4200`` y ``http://127.0.0.1:4200``) para
      habilitar el flujo cross-origin de desarrollo;
    - en produccion devuelve una lista vacia, de modo que no se registre
      ningun middleware CORS por defecto (fail-safe: sin CORS salvo
      configuracion explicita).

    Si la variable define origenes explicitos, estos se respetan tal cual tanto
    en local como en produccion. Los origenes no son secretos.

    :param env: mapeo de variables de entorno; por defecto ``os.environ``.
    :return: lista de origenes permitidos (posiblemente vacia).
    """
    source: Mapping[str, str] = os.environ if env is None else env
    raw = source.get("CORS_ALLOWED_ORIGINS", "")
    origins = [entry.strip() for entry in raw.split(",") if entry.strip()]
    if origins:
        return origins
    if is_local_env(source):
        return list(_DEFAULT_LOCAL_CORS_ORIGINS)
    return []


def get_corporate_allowed_domains(env: Mapping[str, str] | None = None) -> list[str]:
    """Devuelve la allowlist de dominios corporativos permitidos para el camino B.

    Lee la variable opcional ``CORPORATE_ALLOWED_DOMAINS`` (lista separada por
    comas) de las variables de entorno, recorta cada entrada, descarta las vacias
    y normaliza cada dominio a minusculas (los dominios son case-insensitive, a
    diferencia de los origenes CORS). Es fail-safe: si la variable esta ausente o
    queda vacia tras el filtrado devuelve una **lista vacia** en cualquier entorno
    (incluido local), de modo que sin configuracion explicita no se autorice
    ningun dominio corporativo (R9.3). No hay dominios hardcodeados como default.

    El getter solo *provee* la lista normalizada; la semantica de autorizacion es
    de **match exacto** de dominio (sin sufijos ni subdominios) y la aplica el
    consumidor (validacion server-side de POCA-004): un subdominio como
    ``mail.segurosbolivar.com`` NO coincide con ``segurosbolivar.com`` salvo que se
    configure explicitamente. Los dominios no son secretos; el getter no registra
    valores (leakage prevention).

    Esta variable es opcional y NO forma parte de ``REQUIRED_ENV_VARS``: una
    variable requerida abortaria el arranque, contrario al comportamiento
    fail-safe esperado.

    :param env: mapeo de variables de entorno; por defecto ``os.environ``.
    :return: lista de dominios corporativos normalizados (posiblemente vacia).
    """
    source: Mapping[str, str] = os.environ if env is None else env
    raw = source.get("CORPORATE_ALLOWED_DOMAINS", "")
    return [entry.strip().lower() for entry in raw.split(",") if entry.strip()]


def get_session_cookie_secure(env: Mapping[str, str] | None = None) -> bool:
    """Devuelve el atributo ``Secure`` de la session cookie segun el entorno.

    En produccion la cookie siempre se marca ``Secure`` (solo viaja por HTTPS),
    atributo obligatorio del modelo de seguridad (R5.6). SOLO en entorno local
    (``APP_ENV=local``) se relaja a ``False`` para permitir el flujo de la
    cookie sobre HTTP plano durante el desarrollo cross-origin
    (front 4200 -> back 8081), donde no hay TLS. Este relajamiento es exclusivo
    de desarrollo local y nunca aplica en produccion (fail-safe por defecto).

    :param env: mapeo de variables de entorno; por defecto ``os.environ``.
    :return: ``False`` solo en entorno local; ``True`` en produccion.
    """
    return not is_local_env(env)


def get_session_cookie_samesite(env: Mapping[str, str] | None = None) -> str:
    """Devuelve el atributo ``SameSite`` de la session cookie segun el entorno.

    En produccion devuelve ``"strict"`` (la cookie no viaja en solicitudes
    cross-site), el valor mas restrictivo. SOLO en entorno local
    (``APP_ENV=local``) devuelve ``"lax"``: no se usa ``"none"`` porque ese
    valor exigiria ``Secure=True``, incompatible con el HTTP plano del dev
    server. ``"lax"`` permite el envio de la cookie con credenciales en
    navegaciones top-level y solicitudes same-site desde localhost, suficiente
    para el flujo de desarrollo.

    NOTA de desarrollo local (tradeoff): a efectos de ``SameSite`` los distintos
    puertos sobre el mismo dominio registrable ("localhost") se consideran
    same-site, por lo que ``Lax`` permite la cookie en solicitudes same-site;
    pero ``http://localhost:4200`` y ``http://127.0.0.1:8081`` difieren por
    *host*, lo que las convierte en cross-site. Para que el flujo funcione en
    local, el frontend debe alcanzar al backend usando el MISMO hostname (p. ej.
    si la app corre en ``http://localhost:4200``, debe llamar al backend en
    ``http://localhost:8081``), o bien configurar el proxy de Angular. Ver el
    README del backend.

    :param env: mapeo de variables de entorno; por defecto ``os.environ``.
    :return: ``"lax"`` en entorno local; ``"strict"`` en produccion.
    """
    return "lax" if is_local_env(env) else "strict"


def get_session_cookie_name(env: Mapping[str, str] | None = None) -> str:
    """Devuelve el nombre configurado de la session cookie (R5.6).

    Lee ``SESSION_COOKIE_NAME`` de las variables de entorno; su presencia la
    valida `StartupValidator` al arrancar. Si por alguna razon el valor viene
    vacio tras recortar espacios, recurre a un nombre por defecto neutro. El
    nombre de la cookie no es un secreto.

    :param env: mapeo de variables de entorno; por defecto ``os.environ``.
    :return: el nombre de la cookie de sesion.
    """
    source: Mapping[str, str] = os.environ if env is None else env
    name = source.get("SESSION_COOKIE_NAME", "").strip()
    return name or _DEFAULT_SESSION_COOKIE_NAME


def get_session_cookie_max_age_seconds(env: Mapping[str, str] | None = None) -> int:
    """Devuelve la expiracion acotada (en segundos) de la session cookie (R5.6).

    Lee ``SESSION_COOKIE_MAX_AGE_SECONDS`` de las variables de entorno (su
    presencia la valida `StartupValidator`). Si el valor esta ausente, vacio o
    no es un entero positivo, aplica un valor por defecto seguro. La creacion de
    la session cookie vuelve a acotar este valor al rango exigido por el Admin
    SDK (300 s a 2 semanas), de modo que la expiracion siempre queda acotada.

    :param env: mapeo de variables de entorno; por defecto ``os.environ``.
    :return: la expiracion en segundos como entero positivo.
    """
    source: Mapping[str, str] = os.environ if env is None else env
    raw = source.get("SESSION_COOKIE_MAX_AGE_SECONDS", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_SESSION_COOKIE_MAX_AGE_SECONDS
    return value if value > 0 else _DEFAULT_SESSION_COOKIE_MAX_AGE_SECONDS


def get_rate_limit_max_requests(env: Mapping[str, str] | None = None) -> int:
    """Devuelve el maximo de solicitudes permitidas por ventana de rate limiting.

    Lee ``RATE_LIMIT_MAX_REQUESTS`` de las variables de entorno. Si el valor
    esta ausente, vacio o no es un entero positivo, aplica un valor por defecto
    seguro. Este limite es un refuerzo de los bloqueos primarios de Firebase
    (R3.7) y F5 (R4.10), no su sustituto.

    :param env: mapeo de variables de entorno; por defecto ``os.environ``.
    :return: el maximo de solicitudes por ventana como entero positivo.
    """
    source: Mapping[str, str] = os.environ if env is None else env
    raw = source.get("RATE_LIMIT_MAX_REQUESTS", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_RATE_LIMIT_MAX_REQUESTS
    return value if value > 0 else _DEFAULT_RATE_LIMIT_MAX_REQUESTS


def get_rate_limit_window_seconds(env: Mapping[str, str] | None = None) -> int:
    """Devuelve la duracion (en segundos) de la ventana fija de rate limiting.

    Lee ``RATE_LIMIT_WINDOW_SECONDS`` de las variables de entorno. Si el valor
    esta ausente, vacio o no es un entero positivo, aplica un valor por defecto
    seguro. Define la ventana sobre la que se cuenta el numero de solicitudes
    por clave (IP + endpoint) para el refuerzo de rate limiting (R3.7, R4.10).

    :param env: mapeo de variables de entorno; por defecto ``os.environ``.
    :return: la duracion de la ventana en segundos como entero positivo.
    """
    source: Mapping[str, str] = os.environ if env is None else env
    raw = source.get("RATE_LIMIT_WINDOW_SECONDS", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_RATE_LIMIT_WINDOW_SECONDS
    return value if value > 0 else _DEFAULT_RATE_LIMIT_WINDOW_SECONDS


class StartupValidator:
    """Valida la presencia de variables de entorno requeridas al arrancar.

    Se instancia con el conjunto de nombres requeridos y, al invocar
    `validate`, aborta el arranque elevando `MissingEnvironmentVariablesError`
    si alguna variable esta ausente o vacia (R9.4).
    """

    def __init__(self, required: tuple[str, ...] = REQUIRED_ENV_VARS) -> None:
        """Inicializa el validador con los nombres de variables requeridas.

        :param required: nombres de las variables de entorno obligatorias.
        """
        self._required = required

    def validate(self, env: Mapping[str, str] | None = None) -> None:
        """Verifica que todas las variables requeridas esten presentes.

        :param env: mapeo de variables de entorno; por defecto ``os.environ``.
        :raises MissingEnvironmentVariablesError: si falta alguna variable
            requerida o su valor esta vacio. El error solo expone nombres,
            nunca valores (R9.4).
        """
        source: Mapping[str, str] = os.environ if env is None else env
        missing = find_missing_env_vars(source, self._required)
        if missing:
            raise MissingEnvironmentVariablesError(missing)
