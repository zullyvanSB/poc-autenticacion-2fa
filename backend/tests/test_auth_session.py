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
