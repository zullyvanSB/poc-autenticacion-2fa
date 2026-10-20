"""Pruebas unitarias del handler de enrutamiento `POST /api/v1/auth/route` (tarea 3.3).

Verifican el comportamiento del endpoint con un arnes minimo que monta el
`CorrelationIdMiddleware`, los manejadores globales de error y el `auth_router`,
evitando la validacion de variables de entorno de `create_app` para aislar la
logica del handler (no se consumen proveedores reales; R9.1 aplica a tests de
integracion, no a estos unitarios).

Cobertura (Given/When/Then):
- Clasificacion Cliente (correo) y Interno (numero) con `RouteResponse` y
  Correlation-ID (R2.1, R2.2).
- Rechazo generico 400 de identificador no clasificable, sin motivo ni valor
  (R2.4, R1.3, R1.4, R10.7).
- Rechazo 422 generico ante fallo de esquema (vacio, >254, clave inesperada)
  via el manejador global de validacion (R10.6, R10.7).
- Generacion/propagacion del Correlation-ID (R2.6, R10.1).
- No fuga del identificador en el cuerpo ni en los logs del rechazo (R2.5).
"""

from __future__ import annotations

import logging

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from auth.router import router as auth_router
from common.api import API_V1_PREFIX, CORRELATION_ID_HEADER
from common.correlation import CorrelationIdMiddleware
from common.errors import register_error_handlers

ROUTE_PATH = f"{API_V1_PREFIX}/auth/route"


def _build_test_app() -> FastAPI:
    """Construye un arnes minimo con el middleware, errores y el auth_router.

    Replica el cableado de `create_app` relevante para el enrutamiento sin
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
def client() -> TestClient:
    """Cliente de prueba sobre el arnes minimo del enrutamiento.

    :return: `TestClient` listo para ejercitar `POST /api/v1/auth/route`.
    """
    return TestClient(_build_test_app())


def test_route_cliente_para_correo_valido(client: TestClient) -> None:
    """Given un correo valido, When se enruta, Then responde ruta Cliente (R2.1)."""
    # Given / When
    response = client.post(ROUTE_PATH, json={"identifier": "cliente@example.com"})

    # Then
    assert response.status_code == 200
    body = response.json()
    assert body["route_type"] == "cliente"
    assert body["login_method"] == "password_totp"
    assert body["correlation_id"]
    assert response.headers[CORRELATION_ID_HEADER] == body["correlation_id"]


def test_route_corporativo_para_correo_de_dominio_permitido(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given un correo corporativo y allowlist poblada, When se enruta, Then 200 corporativo (AC-1)."""
    # Given: se simula la frontera de configuracion poblando la allowlist que
    # `classify_route` resuelve por defecto, sin leer `os.environ` real.
    monkeypatch.setattr(
        "auth.routing.get_corporate_allowed_domains",
        lambda: ["segurosbolivar.com", "serviciosbolivar.com"],
    )

    # When
    response = client.post(
        ROUTE_PATH, json={"identifier": "persona@segurosbolivar.com"}
    )

    # Then: el handler responde la ruta corporativa con el nuevo login_method.
    assert response.status_code == 200
    body = response.json()
    assert body["route_type"] == "corporativo"
    assert body["login_method"] == "google_redirect"
    assert body["correlation_id"]
    assert response.headers[CORRELATION_ID_HEADER] == body["correlation_id"]


def test_route_cliente_para_correo_no_corporativo_sin_regresion(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given allowlist poblada, When se enruta un correo no corporativo, Then sigue Cliente (AC-3)."""
    # Given: allowlist poblada igual que el caso corporativo.
    monkeypatch.setattr(
        "auth.routing.get_corporate_allowed_domains",
        lambda: ["segurosbolivar.com", "serviciosbolivar.com"],
    )

    # When: un correo de dominio fuera de la allowlist.
    response = client.post(ROUTE_PATH, json={"identifier": "persona@gmail.com"})

    # Then: camino C intacto a nivel de contrato HTTP.
    assert response.status_code == 200
    body = response.json()
    assert body["route_type"] == "cliente"
    assert body["login_method"] == "password_totp"


def test_route_interno_para_numero_valido(client: TestClient) -> None:
    """Given un numero valido, When se enruta, Then responde ruta Interno (R2.2)."""
    # Given / When
    response = client.post(ROUTE_PATH, json={"identifier": "1234567"})

    # Then
    assert response.status_code == 200
    body = response.json()
    assert body["route_type"] == "interno"
    assert body["login_method"] == "saml_redirect"


def test_route_rechazo_generico_para_identificador_no_clasificable(
    client: TestClient,
) -> None:
    """Given un identificador no clasificable, When se enruta, Then 400 generico (R2.4)."""
    # Given: longitud valida (>=5) pero ni correo ni numero de identificacion.
    identifier = "abcdef"

    # When
    response = client.post(ROUTE_PATH, json={"identifier": identifier})

    # Then: error generico 400 sin route_type, sin motivo ni valor recibido.
    assert response.status_code == 400
    body = response.json()
    assert set(body.keys()) == {"detail", "correlation_id"}
    assert "route_type" not in body
    assert identifier not in body["detail"]
    assert body["correlation_id"]
    assert response.headers[CORRELATION_ID_HEADER] == body["correlation_id"]


def test_route_genera_correlation_id_cuando_no_se_provee(client: TestClient) -> None:
    """Given sin header, When se enruta, Then la respuesta trae Correlation-ID (R2.6, R10.1)."""
    # Given / When
    response = client.post(ROUTE_PATH, json={"identifier": "1234567"})

    # Then
    assert response.headers.get(CORRELATION_ID_HEADER)
    assert response.json()["correlation_id"] == response.headers[CORRELATION_ID_HEADER]


def test_route_propaga_correlation_id_entrante(client: TestClient) -> None:
    """Given un Correlation-ID entrante, When se enruta, Then se refleja (R10.1)."""
    # Given
    incoming = "11111111-1111-4111-8111-111111111111"

    # When
    response = client.post(
        ROUTE_PATH,
        json={"identifier": "cliente@example.com"},
        headers={CORRELATION_ID_HEADER: incoming},
    )

    # Then
    assert response.json()["correlation_id"] == incoming
    assert response.headers[CORRELATION_ID_HEADER] == incoming


@pytest.mark.parametrize(
    "payload",
    [
        {"identifier": ""},  # vacio: viola min_length (R1.3)
        {"identifier": "   "},  # solo espacios: tras strip queda vacio (R1.3)
        {"identifier": "a" * 255},  # excede 254 (R1.4)
        {"identifier": "cliente@example.com", "extra": "x"},  # clave inesperada
        {},  # falta el campo requerido
        {"identifier": 12345},  # tipo invalido
    ],
)
def test_route_rechazo_422_para_esquema_invalido(
    client: TestClient, payload: dict
) -> None:
    """Given un cuerpo fuera de esquema, When se enruta, Then 422 generico (R10.6, R10.7)."""
    # Given / When
    response = client.post(ROUTE_PATH, json=payload)

    # Then: el manejador global de validacion responde 422 generico.
    assert response.status_code == 422
    body = response.json()
    assert set(body.keys()) == {"detail", "correlation_id"}
    assert "route_type" not in body


def test_route_no_filtra_identificador_en_logs_de_rechazo(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Given un rechazo, When se registra, Then el log no contiene el identificador (R2.5)."""
    # Given
    identifier = "noclasificable123abc"

    # When
    with caplog.at_level(logging.DEBUG, logger="auth_2fa_poc"):
        response = client.post(ROUTE_PATH, json={"identifier": identifier})

    # Then
    assert response.status_code == 400
    assert all(identifier not in record.getMessage() for record in caplog.records)
    assert all(
        identifier not in str(getattr(record, "args", "")) for record in caplog.records
    )
