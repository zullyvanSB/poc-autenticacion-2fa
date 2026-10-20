"""Pruebas unitarias del handler de sesion `POST /api/v1/auth/session` (tarea 6.2).

Verifican el comportamiento del endpoint con un arnes minimo que monta el
`CorrelationIdMiddleware`, los manejadores globales de error y el `auth_router`,
evitando la validacion de variables de entorno de `create_app` para aislar la
logica del handler. El Firebase Admin SDK se **mockea** por completo
(`verify_id_token` y `create_session_cookie`): nunca se consume un proveedor
real (R9.1 aplica a los tests de integracion, no a estos unitarios).

Cobertura (Given/When/Then):
- Exito: verificacion y creacion de cookie correctas ⇒ 204 con session cookie
  `Secure`/`HttpOnly`/`SameSite=Strict`/`Path=/`/`Max-Age` acotado y SIN token
  ni cookie en el cuerpo (Property 5; R5.6).
- Fallo de verificacion ⇒ 401 generico, sin cookie y `create_session_cookie`
  NO invocado (Property 4; R5.5).
- Fallo en la creacion de la cookie ⇒ 401 generico sin cookie (R5.4).
- Fallo de esquema del cuerpo ⇒ 422 generico via el manejador global (R10.6).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from auth.firebase_auth import InvalidIdTokenError, SessionCookieError
from auth.router import router as auth_router
from common.api import API_V1_PREFIX, CORRELATION_ID_HEADER
from common.correlation import CorrelationIdMiddleware
from common.errors import register_error_handlers

SESSION_PATH = f"{API_V1_PREFIX}/auth/session"

# ID token de ejemplo (no es un JWT real; el Admin SDK esta mockeado). En el
# flujo real nunca se registra ni viaja al cliente en el cuerpo de la respuesta.
_SAMPLE_ID_TOKEN = "sample.id.token.value"

# Session cookie de ejemplo que devuelve el mock de `create_session_cookie`.
_SAMPLE_SESSION_COOKIE = "mocked.session.cookie.value"

# Valores de configuracion de la cookie usados por el handler (mockeados para
# no depender de variables de entorno reales).
_COOKIE_NAME = "session"
_COOKIE_MAX_AGE = 3600


def _build_test_app() -> FastAPI:
    """Construye un arnes minimo con el middleware, errores y el auth_router.

    Replica el cableado de `create_app` relevante para el endpoint de sesion sin
    invocar `StartupValidator`, de modo que las pruebas no dependan de variables
    de entorno.

    :return: la aplicacion FastAPI de prueba.
    """
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app)
    api_v1_router = APIRouter(prefix=API_V1_PREFIX)
    api_v1_router.include_router(auth_router)
    app.include_router(api_v1_router)
    return app


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """Cliente de prueba con la configuracion de cookie mockeada.

    Fija el nombre y la expiracion de la cookie a valores deterministas para
    aislar el handler de las variables de entorno reales.

    :yield: `TestClient` listo para ejercitar `POST /api/v1/auth/session`.
    """
    with patch(
        "auth.router.get_session_cookie_name", return_value=_COOKIE_NAME
    ), patch(
        "auth.router.get_session_cookie_max_age_seconds", return_value=_COOKIE_MAX_AGE
    ), patch(
        "auth.router.get_session_cookie_secure", return_value=True
    ), patch(
        "auth.router.get_session_cookie_samesite", return_value="strict"
    ):
        yield TestClient(_build_test_app())


def test_session_success_sets_secure_cookie_and_empty_body(client: TestClient) -> None:
    """Given token valido, When se crea sesion, Then 204 con cookie segura y sin cuerpo."""
    # Given: la verificacion y la creacion de la cookie tienen exito.
    with patch("auth.router.verify_id_token", return_value={"uid": "u1"}), patch(
        "auth.router.create_session_cookie", return_value=_SAMPLE_SESSION_COOKIE
    ) as mock_create:
        # When
        response = client.post(SESSION_PATH, json={"id_token": _SAMPLE_ID_TOKEN})

    # Then: 204 sin cuerpo (ni token ni cookie en el body) (Property 5, R5.6).
    assert response.status_code == 204
    assert response.content == b""
    assert _SAMPLE_SESSION_COOKIE not in response.text
    assert _SAMPLE_ID_TOKEN not in response.text

    # Then: la cookie se creo con la expiracion acotada configurada.
    mock_create.assert_called_once_with(_SAMPLE_ID_TOKEN, _COOKIE_MAX_AGE)

    # Then: la cookie se entrega con atributos de seguridad obligatorios.
    # Los nombres/valores de atributos de cookie son insensibles a
    # mayusculas/minusculas (RFC 6265bis); Starlette emite `SameSite=strict`.
    set_cookie = response.headers.get("set-cookie")
    assert set_cookie is not None
    normalized = set_cookie.lower()
    assert f"{_COOKIE_NAME}={_SAMPLE_SESSION_COOKIE}".lower() in normalized
    assert "secure" in normalized
    assert "httponly" in normalized
    assert "samesite=strict" in normalized
    assert "path=/" in normalized
    assert f"max-age={_COOKIE_MAX_AGE}" in normalized
    # El valor de la cookie NO viaja en el cuerpo (Property 5).
    assert client.cookies.get(_COOKIE_NAME) == _SAMPLE_SESSION_COOKIE


def test_session_success_local_env_sets_relaxed_cookie() -> None:
    """Given entorno local, When se crea sesion, Then cookie no-Secure + SameSite=lax."""
    # Given: APP_ENV=local => Secure=false, SameSite=lax (solo desarrollo local).
    with patch(
        "auth.router.get_session_cookie_name", return_value=_COOKIE_NAME
    ), patch(
        "auth.router.get_session_cookie_max_age_seconds", return_value=_COOKIE_MAX_AGE
    ), patch(
        "auth.router.get_session_cookie_secure", return_value=False
    ), patch(
        "auth.router.get_session_cookie_samesite", return_value="lax"
    ):
        local_client = TestClient(_build_test_app())
        with patch("auth.router.verify_id_token", return_value={"uid": "u1"}), patch(
            "auth.router.create_session_cookie", return_value=_SAMPLE_SESSION_COOKIE
        ):
            # When
            response = local_client.post(
                SESSION_PATH, json={"id_token": _SAMPLE_ID_TOKEN}
            )

    # Then: 204 y la cookie relaja Secure/SameSite para el flujo HTTP local.
    assert response.status_code == 204
    set_cookie = response.headers.get("set-cookie")
    assert set_cookie is not None
    normalized = set_cookie.lower()
    assert "samesite=lax" in normalized
    assert "httponly" in normalized
    assert "path=/" in normalized
    # Secure NO debe estar presente en entorno local (HTTP plano).
    assert "secure" not in normalized


def test_session_verification_failure_returns_401_and_no_cookie(
    client: TestClient,
) -> None:
    """Given token invalido, When se crea sesion, Then 401 sin cookie ni creacion (Property 4)."""
    # Given: la verificacion falla; la creacion de cookie NO debe invocarse.
    with patch(
        "auth.router.verify_id_token",
        side_effect=InvalidIdTokenError("El token de identidad no es valido."),
    ), patch("auth.router.create_session_cookie") as mock_create:
        # When
        response = client.post(SESSION_PATH, json={"id_token": _SAMPLE_ID_TOKEN})

    # Then: 401 generico, sin establecer sesion (Property 4, R5.5).
    assert response.status_code == 401
    body = response.json()
    assert set(body.keys()) == {"detail", "correlation_id"}
    assert body["correlation_id"]
    assert response.headers[CORRELATION_ID_HEADER] == body["correlation_id"]

    # Then: NO se intento crear la session cookie y NO hay Set-Cookie.
    mock_create.assert_not_called()
    assert "set-cookie" not in {k.lower() for k in response.headers.keys()}
    # El cuerpo generico no filtra el token.
    assert _SAMPLE_ID_TOKEN not in body["detail"]


def test_session_cookie_creation_failure_returns_401(client: TestClient) -> None:
    """Given creacion de cookie fallida, When se crea sesion, Then 401 sin cookie (R5.4)."""
    # Given: la verificacion es exitosa pero la creacion de la cookie falla.
    with patch("auth.router.verify_id_token", return_value={"uid": "u1"}), patch(
        "auth.router.create_session_cookie",
        side_effect=SessionCookieError("No fue posible crear la sesion."),
    ):
        # When
        response = client.post(SESSION_PATH, json={"id_token": _SAMPLE_ID_TOKEN})

    # Then: 401 generico, sin cookie (R5.4).
    assert response.status_code == 401
    assert "set-cookie" not in {k.lower() for k in response.headers.keys()}
    assert set(response.json().keys()) == {"detail", "correlation_id"}


@pytest.mark.parametrize(
    "payload",
    [
        {"id_token": ""},  # vacio: viola min_length=1
        {"id_token": "a" * 4097},  # excede max_length=4096
        {"id_token": "x", "extra": "y"},  # clave inesperada (extra="forbid")
        {},  # falta el campo requerido
        {"id_token": 123},  # tipo invalido
    ],
)
def test_session_rejects_invalid_schema_with_422(
    client: TestClient, payload: dict
) -> None:
    """Given cuerpo fuera de esquema, When se crea sesion, Then 422 generico (R10.6)."""
    # Given: el esquema invalido no debe invocar verificacion ni creacion.
    with patch("auth.router.verify_id_token") as mock_verify, patch(
        "auth.router.create_session_cookie"
    ) as mock_create:
        # When
        response = client.post(SESSION_PATH, json=payload)

    # Then: el manejador global de validacion responde 422 generico.
    assert response.status_code == 422
    assert set(response.json().keys()) == {"detail", "correlation_id"}
    mock_verify.assert_not_called()
    mock_create.assert_not_called()


def test_session_does_not_log_id_token_on_failure(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Given un rechazo, When se registra, Then el log no contiene el ID token (R10.3)."""
    # Given: la verificacion falla.
    with patch(
        "auth.router.verify_id_token",
        side_effect=InvalidIdTokenError("generico"),
    ), patch("auth.router.create_session_cookie"):
        # When
        with caplog.at_level(logging.DEBUG, logger="auth_2fa_poc"):
            response = client.post(SESSION_PATH, json={"id_token": _SAMPLE_ID_TOKEN})

    # Then: ningun registro contiene el valor del ID token.
    assert response.status_code == 401
    assert all(
        _SAMPLE_ID_TOKEN not in record.getMessage() for record in caplog.records
    )
    assert all(
        _SAMPLE_ID_TOKEN not in str(getattr(record, "args", ""))
        for record in caplog.records
    )


# --------------------------------------------------------------------------- #
# Validacion server-side de dominio corporativo (camino B, Google) — POCA-004
# --------------------------------------------------------------------------- #

# Allowlist de prueba inyectada por mock (sin leer os.environ). Simula el valor
# que POCA-001 entrega normalizado a minusculas.
_TEST_ALLOWED_DOMAINS = ["segurosbolivar.com", "serviciosbolivar.com"]


# Centinela para distinguir "email ausente" de "email None".
_ABSENT = object()


def _google_claims(email: Any, extra_firebase: dict | None = None) -> dict:
    """Construye claims de un token de Google con el `email` indicado.

    :param email: valor del claim top-level ``email`` (o un tipo arbitrario para
        casos de ausencia/forma invalida).
    :param extra_firebase: claves adicionales bajo el objeto ``firebase`` (p. ej.
        un ``hd`` corporativo para el caso hd-vs-email).
    :return: diccionario de claims con ``firebase.sign_in_provider == google.com``.
    """
    firebase_claim: dict = {"sign_in_provider": "google.com"}
    if extra_firebase:
        firebase_claim.update(extra_firebase)
    claims: dict = {"firebase": firebase_claim}
    if email is not _ABSENT:
        claims["email"] = email
    return claims


@pytest.fixture()
def google_client() -> Iterator[TestClient]:
    """Cliente de prueba con la allowlist corporativa mockeada (camino B).

    Añade al mock de cookie el parche de ``get_corporate_allowed_domains`` en
    ``auth.router`` para inyectar la allowlist de prueba sin leer el entorno.

    :yield: `TestClient` listo para ejercitar el camino B de `/auth/session`.
    """
    with patch(
        "auth.router.get_session_cookie_name", return_value=_COOKIE_NAME
    ), patch(
        "auth.router.get_session_cookie_max_age_seconds", return_value=_COOKIE_MAX_AGE
    ), patch(
        "auth.router.get_session_cookie_secure", return_value=True
    ), patch(
        "auth.router.get_session_cookie_samesite", return_value="strict"
    ), patch(
        "auth.router.get_corporate_allowed_domains",
        return_value=list(_TEST_ALLOWED_DOMAINS),
    ):
        yield TestClient(_build_test_app())


@pytest.mark.parametrize(
    "email",
    ["u@segurosbolivar.com", "u@serviciosbolivar.com", "User@SegurosBolivar.COM"],
)
def test_session_google_allowed_domain_creates_cookie(
    google_client: TestClient, email: str
) -> None:
    """Given token Google de dominio permitido, When sesion, Then 204 con cookie (AC-1, AC-2)."""
    # Given: verificacion exitosa con claims Google de dominio permitido.
    with patch(
        "auth.router.verify_id_token", return_value=_google_claims(email)
    ), patch(
        "auth.router.create_session_cookie", return_value=_SAMPLE_SESSION_COOKIE
    ) as mock_create:
        # When
        response = google_client.post(SESSION_PATH, json={"id_token": _SAMPLE_ID_TOKEN})

    # Then: 204 y cookie HttpOnly materializada.
    assert response.status_code == 204
    mock_create.assert_called_once_with(_SAMPLE_ID_TOKEN, _COOKIE_MAX_AGE)
    set_cookie = response.headers.get("set-cookie")
    assert set_cookie is not None
    assert "httponly" in set_cookie.lower()


@pytest.mark.parametrize("email", ["u@gmail.com", "u@samtel.eu"])
def test_session_google_disallowed_domain_rejected_generically(
    google_client: TestClient, email: str
) -> None:
    """Given token Google de dominio no permitido, When sesion, Then 401 sin cookie (AC-3)."""
    # Given: claims Google con un dominio fuera de la allowlist.
    with patch(
        "auth.router.verify_id_token", return_value=_google_claims(email)
    ), patch("auth.router.create_session_cookie") as mock_create:
        # When
        response = google_client.post(SESSION_PATH, json={"id_token": _SAMPLE_ID_TOKEN})

    # Then: 401 generico, sin cookie, sin crear la cookie, sin filtrar el correo.
    assert response.status_code == 401
    mock_create.assert_not_called()
    assert "set-cookie" not in {k.lower() for k in response.headers.keys()}
    body = response.json()
    assert set(body.keys()) == {"detail", "correlation_id"}
    assert email not in body["detail"]
    assert email.split("@")[1] not in body["detail"]


@pytest.mark.parametrize(
    "email", ["u@sub.segurosbolivar.com", "u@evil-segurosbolivar.com"]
)
def test_session_google_domain_match_is_exact(
    google_client: TestClient, email: str
) -> None:
    """Given subdominio/prefijo del dominio permitido, When sesion, Then 401 (AC-4)."""
    # Given: claims Google con un dominio que no iguala exacto.
    with patch(
        "auth.router.verify_id_token", return_value=_google_claims(email)
    ), patch("auth.router.create_session_cookie") as mock_create:
        # When
        response = google_client.post(SESSION_PATH, json={"id_token": _SAMPLE_ID_TOKEN})

    # Then: match exacto ⇒ rechazo 401 sin cookie.
    assert response.status_code == 401
    mock_create.assert_not_called()
    assert "set-cookie" not in {k.lower() for k in response.headers.keys()}


def test_session_google_anchors_on_email_not_hd(google_client: TestClient) -> None:
    """Given hd corporativo pero email no permitido, When sesion, Then 401 (AC-5)."""
    # Given: claims con un `hd` corporativo pero email de dominio no permitido.
    claims = _google_claims(
        "u@gmail.com", extra_firebase={"hd": "segurosbolivar.com"}
    )
    with patch("auth.router.verify_id_token", return_value=claims), patch(
        "auth.router.create_session_cookie"
    ) as mock_create:
        # When
        response = google_client.post(SESSION_PATH, json={"id_token": _SAMPLE_ID_TOKEN})

    # Then: la autorizacion se ancla en email (no en hd) ⇒ 401.
    assert response.status_code == 401
    mock_create.assert_not_called()


@pytest.mark.parametrize("email", [_ABSENT, None, "", "sin-arroba"])
def test_session_google_missing_or_invalid_email_rejected(
    google_client: TestClient, email: Any
) -> None:
    """Given token Google sin email extraible, When sesion, Then 401 sin cookie (fail-safe)."""
    # Given: claims Google con email ausente/None/vacio/sin dominio.
    with patch(
        "auth.router.verify_id_token", return_value=_google_claims(email)
    ), patch("auth.router.create_session_cookie") as mock_create:
        # When
        response = google_client.post(SESSION_PATH, json={"id_token": _SAMPLE_ID_TOKEN})

    # Then: no se puede validar el dominio ⇒ 401 fail-safe, sin cookie.
    assert response.status_code == 401
    mock_create.assert_not_called()
    assert "set-cookie" not in {k.lower() for k in response.headers.keys()}


def test_session_google_uses_allowlist_from_getter(google_client: TestClient) -> None:
    """Given la validacion Google, When sesion, Then usa el getter por entorno (AC-6)."""
    # Given: un dominio permitido solo si el getter lo provee (no hardcode).
    with patch(
        "auth.router.verify_id_token",
        return_value=_google_claims("u@serviciosbolivar.com"),
    ), patch(
        "auth.router.create_session_cookie", return_value=_SAMPLE_SESSION_COOKIE
    ), patch(
        "auth.router.get_corporate_allowed_domains", return_value=[]
    ) as mock_getter:
        # When: con allowlist vacia del getter, incluso un dominio corporativo se rechaza.
        response = google_client.post(SESSION_PATH, json={"id_token": _SAMPLE_ID_TOKEN})

    # Then: la validacion consulto el getter y, al estar vacio, rechazo (fail-safe).
    assert response.status_code == 401
    mock_getter.assert_called_once()


def test_session_google_empty_allowlist_rejects(google_client: TestClient) -> None:
    """Given allowlist vacia, When sesion Google, Then 401 (fail-safe, defensa en profundidad)."""
    with patch(
        "auth.router.verify_id_token",
        return_value=_google_claims("u@segurosbolivar.com"),
    ), patch("auth.router.create_session_cookie") as mock_create, patch(
        "auth.router.get_corporate_allowed_domains", return_value=[]
    ):
        response = google_client.post(SESSION_PATH, json={"id_token": _SAMPLE_ID_TOKEN})

    assert response.status_code == 401
    mock_create.assert_not_called()


def test_session_google_rejection_does_not_log_email(
    google_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Given rechazo Google, When se registra, Then el log no contiene el correo (R10.3, leakage)."""
    # Given: un correo distintivo de dominio no permitido.
    sensitive_email = "persona.secreta@gmail.com"
    with patch(
        "auth.router.verify_id_token",
        return_value=_google_claims(sensitive_email),
    ), patch("auth.router.create_session_cookie"):
        # When
        with caplog.at_level(logging.DEBUG, logger="auth_2fa_poc"):
            response = google_client.post(
                SESSION_PATH, json={"id_token": _SAMPLE_ID_TOKEN}
            )

    # Then: ningun registro contiene el correo ni su dominio.
    assert response.status_code == 401
    assert all(
        sensitive_email not in record.getMessage()
        and "gmail.com" not in record.getMessage()
        for record in caplog.records
    )
    assert all(
        sensitive_email not in str(getattr(record, "args", ""))
        for record in caplog.records
    )


# --------------------------------------------------------------------------- #
# Regresion: proveedores NO Google no aplican la validacion de dominio (AC-7)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "claims",
    [
        {"uid": "u1"},  # mock existente sin objeto firebase
        {"firebase": {"sign_in_provider": "password"}},  # Cliente
        {"firebase": {"sign_in_provider": "saml.f5apm"}},  # Interno
        {"firebase": {"sign_in_provider": "password"}, "email": "u@gmail.com"},  # password con email no permitido
        {"firebase": "no-es-dict"},  # firebase no-dict ⇒ proveedor no resoluble
    ],
)
def test_session_non_google_providers_skip_domain_validation(
    google_client: TestClient, claims: dict
) -> None:
    """Given proveedor NO Google, When sesion, Then 204 sin validar dominio (AC-7, No-Objetivos)."""
    # Given: claims de un proveedor distinto a google.com (incluye email no permitido
    # que, por NO ser Google, no debe desencadenar la validacion de dominio).
    with patch("auth.router.verify_id_token", return_value=claims), patch(
        "auth.router.create_session_cookie", return_value=_SAMPLE_SESSION_COOKIE
    ) as mock_create:
        # When
        response = google_client.post(SESSION_PATH, json={"id_token": _SAMPLE_ID_TOKEN})

    # Then: flujo actual intacto ⇒ 204 con cookie; sin regresion.
    assert response.status_code == 204
    mock_create.assert_called_once()
