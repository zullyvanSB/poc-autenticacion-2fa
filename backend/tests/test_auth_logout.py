"""Pruebas unitarias del handler de logout `POST /api/v1/auth/logout` (tarea 8.1).

Verifican que el cierre de sesion sea **idempotente** (Property 9, R6.5): con o
sin sesion valida, y en una o varias invocaciones, el sistema queda en el mismo
estado final "sin sesion" (cookie eliminada y, cuando hay un uid resoluble,
tokens de refresco revocados). El Firebase Admin SDK se **mockea** por completo
(`verify_session_cookie` y `revoke_session`): nunca se consume un proveedor real
(R9.1 aplica a los tests de integracion, no a estos unitarios).

Cobertura (Given/When/Then):
- Sesion valida: se revocan los tokens de refresco y se elimina la cookie
  (Set-Cookie con Max-Age=0, Path=/, Secure, HttpOnly, SameSite=Strict); 204.
- Sin cookie o cookie invalida: NO se revoca, pero igualmente se elimina la
  cookie y se responde 204 (idempotente).
- Multiples invocaciones: cada una deja el mismo estado final "sin sesion".
- No fuga: ni el valor de la cookie ni el uid aparecen en los registros (R10.3).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from auth.firebase_auth import InvalidSessionCookieError, SessionRevocationError
from auth.router import router as auth_router
from common.api import API_V1_PREFIX, CORRELATION_ID_HEADER
from common.correlation import CorrelationIdMiddleware
from common.errors import register_error_handlers

LOGOUT_PATH = f"{API_V1_PREFIX}/auth/logout"

# Nombre determinista de la cookie de sesion (mockeado para no depender de
# variables de entorno reales).
_COOKIE_NAME = "session"

# Valor de session cookie de ejemplo. No es una cookie real: el Admin SDK esta
# mockeado. Nunca debe aparecer en los registros (R10.3).
_SAMPLE_SESSION_COOKIE = "mocked.session.cookie.value"

# uid de ejemplo devuelto por el mock de `verify_session_cookie`. Nunca debe
# aparecer en los registros (R10.3).
_SAMPLE_UID = "user-uid-1234"


def _build_test_app() -> FastAPI:
    """Construye un arnes minimo con el middleware, errores y el auth_router.

    Replica el cableado de `create_app` relevante para el endpoint de logout sin
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
    """Cliente de prueba con el nombre de cookie mockeado a un valor determinista.

    :yield: `TestClient` listo para ejercitar `POST /api/v1/auth/logout`.
    """
    with patch(
        "auth.router.get_session_cookie_name", return_value=_COOKIE_NAME
    ), patch(
        "auth.router.get_session_cookie_secure", return_value=True
    ), patch(
        "auth.router.get_session_cookie_samesite", return_value="strict"
    ):
        yield TestClient(_build_test_app())


def _assert_cookie_cleared(set_cookie: str | None) -> None:
    """Asegura que el header Set-Cookie elimina la cookie con atributos seguros.

    :param set_cookie: valor del header ``Set-Cookie`` de la respuesta.
    """
    assert set_cookie is not None
    normalized = set_cookie.lower()
    # Max-Age=0 => el navegador descarta la cookie de inmediato (R6.5).
    assert "max-age=0" in normalized
    assert "path=/" in normalized
    assert "secure" in normalized
    assert "httponly" in normalized
    assert "samesite=strict" in normalized


def test_logout_with_valid_session_revokes_and_clears_cookie(
    client: TestClient,
) -> None:
    """Given sesion valida, When logout, Then revoca tokens y limpia la cookie (Property 9)."""
    # Given: la cookie es valida (resuelve un uid) y la revocacion tiene exito.
    with patch(
        "auth.router.firebase_auth.verify_session_cookie",
        return_value={"uid": _SAMPLE_UID},
    ), patch("auth.router.revoke_session") as mock_revoke:
        client.cookies.set(_COOKIE_NAME, _SAMPLE_SESSION_COOKIE)
        # When
        response = client.post(LOGOUT_PATH)

    # Then: 204 sin cuerpo.
    assert response.status_code == 204
    assert response.content == b""

    # Then: se revocaron los tokens de refresco del uid resuelto.
    mock_revoke.assert_called_once_with(_SAMPLE_UID)

    # Then: la cookie se elimina (Max-Age=0) con atributos seguros.
    _assert_cookie_cleared(response.headers.get("set-cookie"))


def test_logout_without_cookie_still_clears_and_returns_204(
    client: TestClient,
) -> None:
    """Given sin cookie, When logout, Then no revoca pero limpia la cookie; 204 (idempotente)."""
    # Given: no hay cookie de sesion; la verificacion no debe invocarse.
    with patch(
        "auth.router.firebase_auth.verify_session_cookie"
    ) as mock_verify, patch("auth.router.revoke_session") as mock_revoke:
        # When
        response = client.post(LOGOUT_PATH)

    # Then: 204 y la cookie se elimina igualmente (estado "sin sesion").
    assert response.status_code == 204
    mock_verify.assert_not_called()
    mock_revoke.assert_not_called()
    _assert_cookie_cleared(response.headers.get("set-cookie"))


def test_logout_with_invalid_session_still_clears_and_returns_204(
    client: TestClient,
) -> None:
    """Given cookie invalida, When logout, Then no revoca pero limpia la cookie; 204."""
    # Given: la cookie no supera la verificacion; no debe revocarse.
    with patch(
        "auth.router.firebase_auth.verify_session_cookie",
        side_effect=InvalidSessionCookieError("La sesion no es valida."),
    ), patch("auth.router.revoke_session") as mock_revoke:
        client.cookies.set(_COOKIE_NAME, _SAMPLE_SESSION_COOKIE)
        # When
        response = client.post(LOGOUT_PATH)

    # Then: 204, sin revocar, con la cookie eliminada (idempotente).
    assert response.status_code == 204
    mock_revoke.assert_not_called()
    _assert_cookie_cleared(response.headers.get("set-cookie"))


def test_logout_is_idempotent_across_multiple_invocations(
    client: TestClient,
) -> None:
    """Given una sesion, When logout varias veces, Then el estado final es el mismo (Property 9)."""
    # Given: cookie valida en cada invocacion; la revocacion es idempotente.
    with patch(
        "auth.router.firebase_auth.verify_session_cookie",
        return_value={"uid": _SAMPLE_UID},
    ), patch("auth.router.revoke_session") as mock_revoke:
        client.cookies.set(_COOKIE_NAME, _SAMPLE_SESSION_COOKIE)
        # When: se invoca logout multiples veces.
        first = client.post(LOGOUT_PATH)
        # El TestClient aplica el Set-Cookie: tras el primer logout la cookie
        # queda vacia, por lo que las llamadas siguientes no resuelven uid.
        second = client.post(LOGOUT_PATH)
        third = client.post(LOGOUT_PATH)

    # Then: toda invocacion deja el mismo estado final (204 + cookie eliminada).
    for response in (first, second, third):
        assert response.status_code == 204
        _assert_cookie_cleared(response.headers.get("set-cookie"))


def test_logout_clears_cookie_even_when_revocation_fails(
    client: TestClient,
) -> None:
    """Given revocacion fallida, When logout, Then igual limpia la cookie; 204."""
    # Given: la cookie es valida pero la revocacion de tokens falla.
    with patch(
        "auth.router.firebase_auth.verify_session_cookie",
        return_value={"uid": _SAMPLE_UID},
    ), patch(
        "auth.router.revoke_session",
        side_effect=SessionRevocationError("No fue posible cerrar la sesion."),
    ):
        client.cookies.set(_COOKIE_NAME, _SAMPLE_SESSION_COOKIE)
        # When
        response = client.post(LOGOUT_PATH)

    # Then: el cierre no se interrumpe: la cookie se elimina y responde 204.
    assert response.status_code == 204
    _assert_cookie_cleared(response.headers.get("set-cookie"))


def test_logout_sets_correlation_id_header(client: TestClient) -> None:
    """Given una solicitud, When logout, Then la respuesta incluye el Correlation-ID (R10.1)."""
    # Given/When
    with patch(
        "auth.router.firebase_auth.verify_session_cookie",
        return_value={"uid": _SAMPLE_UID},
    ), patch("auth.router.revoke_session"):
        client.cookies.set(_COOKIE_NAME, _SAMPLE_SESSION_COOKIE)
        response = client.post(LOGOUT_PATH)

    # Then: toda respuesta lleva un Correlation-ID.
    assert response.status_code == 204
    assert response.headers.get(CORRELATION_ID_HEADER)


def test_logout_does_not_log_cookie_or_uid(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Given un logout, When se registra, Then el log no contiene la cookie ni el uid (R10.3)."""
    # Given: sesion valida con uid resoluble.
    with patch(
        "auth.router.firebase_auth.verify_session_cookie",
        return_value={"uid": _SAMPLE_UID},
    ), patch("auth.router.revoke_session"):
        client.cookies.set(_COOKIE_NAME, _SAMPLE_SESSION_COOKIE)
        # When
        with caplog.at_level(logging.DEBUG, logger="auth_2fa_poc"):
            response = client.post(LOGOUT_PATH)

    # Then: ningun registro contiene el valor de la cookie ni el uid.
    assert response.status_code == 204
    for record in caplog.records:
        message = record.getMessage()
        assert _SAMPLE_SESSION_COOKIE not in message
        assert _SAMPLE_UID not in message
        assert _SAMPLE_SESSION_COOKIE not in str(getattr(record, "args", ""))
        assert _SAMPLE_UID not in str(getattr(record, "args", ""))
