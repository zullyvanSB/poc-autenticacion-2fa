"""Pruebas unitarias del handler del dashboard `GET /api/v1/dashboard` (tarea 7.1).

Verifican el comportamiento del endpoint con un arnes minimo que monta el
`CorrelationIdMiddleware`, los manejadores globales de error y el
`dashboard_router`, evitando la validacion de variables de entorno de
`create_app` para aislar la logica del handler. El Firebase Admin SDK se
**mockea** por completo (`verify_session_cookie`): nunca se consume un proveedor
real (R9.1 aplica a los tests de integracion, no a estos unitarios).

Cobertura (Given/When/Then):
- Sesion valida ⇒ 200 con `display_name`, `user_type` y claims enmascarados
  (correo/documento a <=4 caracteres de sufijo), aplicando allowlist de campos
  (Property 6; R6.1, R6.4).
- Sesion valida sin claims presentables ⇒ 200 con `claims` vacio (R6.6).
- Cookie ausente ⇒ 401 generico sin datos ni Set-Cookie (Property 6; R6.3).
- Cookie invalida/expirada/revocada ⇒ 401 generico sin datos (Property 6; R6.3).
- La respuesta 401 no filtra claims ni nombre y no reemite la cookie (R6.3).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from auth.firebase_auth import InvalidSessionCookieError
from common.api import API_V1_PREFIX, CORRELATION_ID_HEADER
from common.correlation import CorrelationIdMiddleware
from common.errors import register_error_handlers
from dashboard.router import router as dashboard_router

DASHBOARD_PATH = f"{API_V1_PREFIX}/dashboard"

# Nombre de la cookie de sesion usado por el handler (mockeado para no depender
# de variables de entorno reales).
_COOKIE_NAME = "session"

# Session cookie de ejemplo (no es una cookie real; el Admin SDK esta mockeado).
_SAMPLE_SESSION_COOKIE = "mocked.session.cookie.value"

# Claims de ejemplo de un Cliente (proveedor `password`) con PII a enmascarar.
_CLIENTE_EMAIL = "persona@example.com"
_CLIENTE_CLAIMS = {
    "uid": "u1",
    "name": "Ana Perez",
    "email": _CLIENTE_EMAIL,
    "firebase": {"sign_in_provider": "password"},
}

# Claims de ejemplo de un Interno (proveedor `saml.<id>`) con documento a
# enmascarar y sin nombre presentable (para validar el display name por defecto).
_INTERNO_DOCUMENT = "1098765432"
_INTERNO_CLAIMS = {
    "uid": "u2",
    "document": _INTERNO_DOCUMENT,
    "firebase": {"sign_in_provider": "saml.f5apm"},
}

# Claims validos pero sin ningun campo presentable (allowlist vacia) (R6.6).
_NO_PRESENTABLE_CLAIMS = {
    "uid": "u3",
    "firebase": {"sign_in_provider": "password"},
}

# Claims de ejemplo de un usuario Corporativo (camino B, proveedor
# `google.com`). Google entrega `name` y `email` como claims TOP-LEVEL (igual
# que la ruta Cliente/password), NO como atributos SAML. Incluye un claim extra
# fuera de la allowlist (`picture`) para verificar que no se filtra (FR-4).
_GOOGLE_EMAIL = "ana.gomez@segurosbolivar.com"
_GOOGLE_NAME = "Ana Gomez"
_CORPORATIVO_GOOGLE_CLAIMS = {
    "uid": "u4",
    "name": _GOOGLE_NAME,
    "email": _GOOGLE_EMAIL,
    "picture": "https://example.com/avatar.png",
    "firebase": {"sign_in_provider": "google.com"},
}

# Claims de un Interno (SAML) tal como los expone Identity Platform cuando el
# IdP (F5 APM) envia NameID `persistent` y el correo/nombre como ATRIBUTOS
# bajo `firebase.sign_in_attributes` (con las URIs de WS-Fed/SAML). No hay
# claims top-level `email` ni `name`.
_SAML_INTERNO_EMAIL = "interno.persona@segurosbolivar.com"
_SAML_INTERNO_FULLNAME = "Carlos Ramirez"
_INTERNO_SAML_CLAIMS = {
    "uid": "napm-persistent-uid-xyz",
    "firebase": {
        "sign_in_provider": "saml.f5apm",
        "sign_in_attributes": {
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/mail": _SAML_INTERNO_EMAIL,
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/fullName": _SAML_INTERNO_FULLNAME,
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname": "Carlos",
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/sn": "Ramirez",
        },
    },
}


def _build_test_app() -> FastAPI:
    """Construye un arnes minimo con el middleware, errores y el dashboard_router.

    Replica el cableado de `create_app` relevante para el endpoint del dashboard
    sin invocar `StartupValidator`, de modo que las pruebas no dependan de
    variables de entorno.

    :return: la aplicacion FastAPI de prueba.
    """
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app)
    api_v1_router = APIRouter(prefix=API_V1_PREFIX)
    api_v1_router.include_router(dashboard_router)
    app.include_router(api_v1_router)
    return app


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """Cliente de prueba con el nombre de cookie de sesion mockeado.

    :yield: `TestClient` listo para ejercitar `GET /api/v1/dashboard`.
    """
    with patch(
        "dashboard.router.get_session_cookie_name", return_value=_COOKIE_NAME
    ):
        yield TestClient(_build_test_app())


def _get_with_cookie(client: TestClient, cookie_value: str | None):
    """Ejecuta GET /dashboard fijando la cookie en la instancia del cliente.

    Evita el `DeprecationWarning` de pasar cookies por solicitud en Starlette:
    la cookie se fija en el cliente, se ejecuta la peticion y luego se limpia
    para no filtrar estado entre casos de prueba.

    :param client: `TestClient` del arnes de prueba.
    :param cookie_value: valor de la session cookie a fijar, o ``None`` para no
        enviar ninguna cookie.
    :return: la respuesta HTTP de la peticion.
    """
    if cookie_value is not None:
        client.cookies.set(_COOKIE_NAME, cookie_value)
    try:
        return client.get(DASHBOARD_PATH)
    finally:
        client.cookies.clear()


def test_dashboard_valid_session_returns_masked_claims(client: TestClient) -> None:
    """Given sesion valida de Cliente, When GET dashboard, Then 200 con claims enmascarados."""
    # Given: la cookie valida verifica con claims de Cliente.
    with patch(
        "dashboard.router.verify_session_cookie", return_value=_CLIENTE_CLAIMS
    ) as mock_verify:
        # When
        response = _get_with_cookie(client, _SAMPLE_SESSION_COOKIE)

    # Then: 200 con los datos del dashboard.
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "display_name",
        "user_type",
        "user_type_label",
        "claims",
    }
    assert body["display_name"] == "Ana Perez"
    assert body["user_type"] == "cliente"
    # No-regresion: la etiqueta presentable del Cliente (POCA-001) (FR-5).
    assert body["user_type_label"] == "Cliente"

    # Then: el correo viaja enmascarado a <=4 caracteres de sufijo (R6.4).
    claims = {claim["name"]: claim["value"] for claim in body["claims"]}
    assert claims["name"] == "Ana Perez"
    assert claims["email"].endswith(".com")
    assert claims["email"] != _CLIENTE_EMAIL
    assert claims["email"].startswith("*")
    # El correo crudo NO aparece en ninguna parte de la respuesta (leakage).
    assert _CLIENTE_EMAIL not in response.text

    # Then: la cookie se valido server-side con el valor recibido.
    mock_verify.assert_called_once_with(_SAMPLE_SESSION_COOKIE)


def test_dashboard_masks_document_and_defaults_display_name(
    client: TestClient,
) -> None:
    """Given sesion de Interno sin nombre, When GET dashboard, Then documento enmascarado y nombre por defecto."""
    # Given: la cookie valida verifica con claims de Interno (documento, sin name).
    with patch(
        "dashboard.router.verify_session_cookie", return_value=_INTERNO_CLAIMS
    ):
        # When
        response = _get_with_cookie(client, _SAMPLE_SESSION_COOKIE)

    # Then: 200, tipo Interno y nombre por defecto (no se exponen campos vacios).
    assert response.status_code == 200
    body = response.json()
    assert body["user_type"] == "interno"
    assert body["user_type_label"] == "Colaborador interno"
    assert body["display_name"] == "Usuario"

    # Then: el documento viaja enmascarado dejando a lo sumo 4 ultimos digitos.
    claims = {claim["name"]: claim["value"] for claim in body["claims"]}
    assert claims["document"].endswith(_INTERNO_DOCUMENT[-4:])
    assert claims["document"] != _INTERNO_DOCUMENT
    assert claims["document"].startswith("*")
    assert _INTERNO_DOCUMENT not in response.text


def test_dashboard_interno_saml_maps_email_and_name_from_attributes(
    client: TestClient,
) -> None:
    """Given Interno SAML con correo/nombre en sign_in_attributes, Then se mapean y el correo se enmascara."""
    # Given: la cookie valida verifica con claims de Interno SAML (atributos).
    with patch(
        "dashboard.router.verify_session_cookie", return_value=_INTERNO_SAML_CLAIMS
    ):
        # When
        response = _get_with_cookie(client, _SAMPLE_SESSION_COOKIE)

    # Then: 200, tipo Interno y el nombre tomado del atributo fullName.
    assert response.status_code == 200
    body = response.json()
    assert body["user_type"] == "interno"
    assert body["user_type_label"] == "Colaborador interno"
    assert body["display_name"] == _SAML_INTERNO_FULLNAME

    # Then: el correo se tomo del atributo SAML `mail` y viaja ENMASCARADO.
    claims = {claim["name"]: claim["value"] for claim in body["claims"]}
    assert "email" in claims
    assert claims["email"].endswith(".com")
    assert claims["email"] != _SAML_INTERNO_EMAIL
    assert claims["email"].startswith("*")
    # El correo crudo NO aparece en ninguna parte de la respuesta (leakage).
    assert _SAML_INTERNO_EMAIL not in response.text


def test_dashboard_google_session_returns_corporativo_label_and_masked_email(
    client: TestClient,
) -> None:
    """Given sesion de Google (camino B), When GET dashboard, Then corporativo con etiqueta y email enmascarado.

    Cubre AC-1/AC-2/FR-1/FR-2/FR-3: para `sign_in_provider == "google.com"` con
    `name`/`email` top-level, el dashboard devuelve `user_type` corporativo, la
    etiqueta presentable "Usuario corporativo", `display_name` igual al claim
    `name` y el `email` enmascarado sin exponer el correo crudo (leakage).
    """
    # Given: la cookie valida verifica con claims de Google (corporativo).
    with patch(
        "dashboard.router.verify_session_cookie",
        return_value=_CORPORATIVO_GOOGLE_CLAIMS,
    ):
        # When
        response = _get_with_cookie(client, _SAMPLE_SESSION_COOKIE)

    # Then: 200, tipo corporativo, etiqueta presentable y nombre del claim name.
    assert response.status_code == 200
    body = response.json()
    assert body["user_type"] == "corporativo"
    assert body["user_type_label"] == "Usuario corporativo"
    assert body["display_name"] == _GOOGLE_NAME

    # Then: el correo de Google (top-level) viaja ENMASCARADO (<=4 de sufijo).
    claims = {claim["name"]: claim["value"] for claim in body["claims"]}
    assert claims["name"] == _GOOGLE_NAME
    assert claims["email"].endswith(".com")
    assert claims["email"] != _GOOGLE_EMAIL
    assert claims["email"].startswith("*")
    # El correo crudo NO aparece en ninguna parte de la respuesta (leakage).
    assert _GOOGLE_EMAIL not in response.text


def test_dashboard_google_session_drops_claims_outside_allowlist(
    client: TestClient,
) -> None:
    """Given sesion de Google con claim fuera de la allowlist, Then ese claim no sale (FR-4)."""
    # Given: los claims de Google incluyen `picture`, fuera de _PRESENTABLE_CLAIMS.
    with patch(
        "dashboard.router.verify_session_cookie",
        return_value=_CORPORATIVO_GOOGLE_CLAIMS,
    ):
        # When
        response = _get_with_cookie(client, _SAMPLE_SESSION_COOKIE)

    # Then: solo salen claims de la allowlist; `picture` nunca se expone.
    assert response.status_code == 200
    body = response.json()
    claim_names = {claim["name"] for claim in body["claims"]}
    assert "picture" not in claim_names
    assert _CORPORATIVO_GOOGLE_CLAIMS["picture"] not in response.text


def test_dashboard_valid_session_without_presentable_claims_returns_empty(
    client: TestClient,
) -> None:
    """Given sesion valida sin claims presentables, When GET dashboard, Then claims vacio (R6.6)."""
    # Given: la cookie valida verifica con claims sin campos presentables.
    with patch(
        "dashboard.router.verify_session_cookie",
        return_value=_NO_PRESENTABLE_CLAIMS,
    ):
        # When
        response = _get_with_cookie(client, _SAMPLE_SESSION_COOKIE)

    # Then: 200 con lista de claims vacia para que el frontend muestre ausencia.
    assert response.status_code == 200
    body = response.json()
    assert body["claims"] == []
    assert body["user_type"] == "cliente"


def test_dashboard_missing_cookie_returns_401_no_data(client: TestClient) -> None:
    """Given sin cookie de sesion, When GET dashboard, Then 401 generico sin datos (Property 6)."""
    # Given: no se envia cookie; la verificacion NO debe invocarse.
    with patch("dashboard.router.verify_session_cookie") as mock_verify:
        # When
        response = _get_with_cookie(client, None)

    # Then: 401 generico, sin datos del dashboard.
    assert response.status_code == 401
    body = response.json()
    assert set(body.keys()) == {"detail", "correlation_id"}
    assert body["correlation_id"]
    assert response.headers[CORRELATION_ID_HEADER] == body["correlation_id"]

    # Then: no se intento validar la sesion y no se reemite cookie.
    mock_verify.assert_not_called()
    assert "set-cookie" not in {k.lower() for k in response.headers.keys()}
    # No se filtran claims ni nombre en el cuerpo del 401.
    assert "display_name" not in body
    assert "claims" not in body


def test_dashboard_invalid_session_returns_401_no_data(client: TestClient) -> None:
    """Given cookie invalida/expirada/revocada, When GET dashboard, Then 401 sin datos (Property 6)."""
    # Given: la validacion de la cookie falla.
    with patch(
        "dashboard.router.verify_session_cookie",
        side_effect=InvalidSessionCookieError("La sesion no es valida."),
    ):
        # When
        response = _get_with_cookie(client, _SAMPLE_SESSION_COOKIE)

    # Then: 401 generico, sin datos del dashboard ni cookie reemitida (R6.3).
    assert response.status_code == 401
    body = response.json()
    assert set(body.keys()) == {"detail", "correlation_id"}
    assert "set-cookie" not in {k.lower() for k in response.headers.keys()}
    assert "display_name" not in body
    assert "claims" not in body
    # El cuerpo generico no revela la causa ni el valor de la cookie.
    assert _SAMPLE_SESSION_COOKIE not in response.text


def test_dashboard_does_not_log_cookie_or_pii_on_failure(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Given un rechazo, When se registra, Then el log no contiene la cookie (R10.3)."""
    # Given: la validacion de la cookie falla.
    with patch(
        "dashboard.router.verify_session_cookie",
        side_effect=InvalidSessionCookieError("generico"),
    ):
        # When
        with caplog.at_level(logging.DEBUG, logger="auth_2fa_poc"):
            response = _get_with_cookie(client, _SAMPLE_SESSION_COOKIE)

    # Then: ningun registro contiene el valor de la cookie de sesion.
    assert response.status_code == 401
    assert all(
        _SAMPLE_SESSION_COOKIE not in record.getMessage()
        for record in caplog.records
    )
