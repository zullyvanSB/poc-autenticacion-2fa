"""Pruebas unitarias del refuerzo de rate limiting en memoria (tarea 8.3).

Cubren dos niveles:

1. El limitador puro `InMemoryRateLimiter` con un reloj inyectado y determinista:
   - solicitudes dentro del limite se permiten;
   - al superar el limite se rechaza con `Retry-After` positivo;
   - la ventana se reinicia tras expirar el TTL;
   - claves distintas (ambito/cliente) se cuentan por separado;
   - validacion de parametros de construccion.

2. La aplicacion del refuerzo en los endpoints `POST /api/v1/auth/route` y
   `POST /api/v1/auth/session`, montando un `InMemoryRateLimiter` con limite 1
   en `app.state` y el Firebase Admin SDK **mockeado**: la primera solicitud
   pasa y la segunda recibe un `429` generico con `Retry-After`, sin filtrar el
   limite ni la ventana (R3.7, R4.10, R10.4, R10.7).

El refuerzo no sustituye los bloqueos primarios de Firebase/F5; estas pruebas
verifican solo el comportamiento del contador del backend sin proveedores
reales.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from auth.router import router as auth_router
from common.api import API_V1_PREFIX, CORRELATION_ID_HEADER
from common.correlation import CorrelationIdMiddleware
from common.errors import register_error_handlers
from common.rate_limit import (
    InMemoryRateLimiter,
    build_rate_limit_key,
    client_key_from_host,
)

ROUTE_PATH = f"{API_V1_PREFIX}/auth/route"
SESSION_PATH = f"{API_V1_PREFIX}/auth/session"

# Identificador de correo valido que `classify_route` acepta como Cliente.
_VALID_EMAIL = "cliente@example.com"

# ID token de ejemplo (el Admin SDK esta mockeado; no es un JWT real).
_SAMPLE_ID_TOKEN = "sample.id.token.value"


class _FakeClock:
    """Reloj monotono controlable para pruebas deterministas del limitador.

    Permite avanzar el tiempo manualmente para ejercitar el reinicio de la
    ventana por TTL sin esperas reales.
    """

    def __init__(self) -> None:
        """Inicializa el reloj en el instante cero."""
        self._now = 0.0

    def __call__(self) -> float:
        """Devuelve el instante actual simulado.

        :return: el valor del reloj en segundos.
        """
        return self._now

    def advance(self, seconds: float) -> None:
        """Avanza el reloj simulado la cantidad de segundos indicada.

        :param seconds: segundos a sumar al instante actual.
        """
        self._now += seconds


# --------------------------------------------------------------------------- #
# Nivel 1: limitador puro
# --------------------------------------------------------------------------- #


def test_requests_under_limit_are_allowed() -> None:
    """Given limite N, When llegan N solicitudes, Then todas se permiten."""
    # Given
    clock = _FakeClock()
    limiter = InMemoryRateLimiter(max_requests=3, window_seconds=60, time_source=clock)

    # When / Then: las 3 primeras solicitudes de la misma clave se permiten.
    for _ in range(3):
        decision = limiter.check("auth.route:1.2.3.4")
        assert decision.allowed is True
        assert decision.retry_after_seconds == 0


def test_request_over_limit_is_rejected_with_retry_after() -> None:
    """Given limite agotado, When llega otra solicitud, Then 429 con Retry-After."""
    # Given: una ventana de 60 s con limite 2, ya agotada.
    clock = _FakeClock()
    limiter = InMemoryRateLimiter(max_requests=2, window_seconds=60, time_source=clock)
    key = "auth.session:1.2.3.4"
    assert limiter.check(key).allowed is True
    assert limiter.check(key).allowed is True

    # When: la tercera solicitud dentro de la ventana.
    decision = limiter.check(key)

    # Then: se rechaza con un Retry-After positivo y acotado por la ventana.
    assert decision.allowed is False
    assert 0 < decision.retry_after_seconds <= 60


def test_window_resets_after_ttl() -> None:
    """Given ventana expirada, When llega una solicitud, Then se permite de nuevo."""
    # Given: limite 1 agotado dentro de una ventana de 30 s.
    clock = _FakeClock()
    limiter = InMemoryRateLimiter(max_requests=1, window_seconds=30, time_source=clock)
    key = "auth.route:1.2.3.4"
    assert limiter.check(key).allowed is True
    assert limiter.check(key).allowed is False

    # When: transcurre el TTL completo de la ventana.
    clock.advance(30)

    # Then: la ventana se reinicia y la solicitud vuelve a permitirse.
    decision = limiter.check(key)
    assert decision.allowed is True
    assert decision.retry_after_seconds == 0


def test_distinct_keys_are_counted_independently() -> None:
    """Given dos clientes, When agotan su cupo, Then no interfieren entre si."""
    # Given: limite 1 por clave.
    clock = _FakeClock()
    limiter = InMemoryRateLimiter(max_requests=1, window_seconds=60, time_source=clock)

    # When: dos IP distintas consumen cada una su cupo.
    assert limiter.check("auth.route:1.1.1.1").allowed is True
    assert limiter.check("auth.route:2.2.2.2").allowed is True

    # Then: cada clave se agota de forma independiente.
    assert limiter.check("auth.route:1.1.1.1").allowed is False
    assert limiter.check("auth.route:2.2.2.2").allowed is False


def test_reset_clears_state() -> None:
    """Given cupo agotado, When se reinicia el estado, Then se permite de nuevo."""
    # Given
    clock = _FakeClock()
    limiter = InMemoryRateLimiter(max_requests=1, window_seconds=60, time_source=clock)
    key = "auth.route:1.2.3.4"
    assert limiter.check(key).allowed is True
    assert limiter.check(key).allowed is False

    # When
    limiter.reset()

    # Then
    assert limiter.check(key).allowed is True


@pytest.mark.parametrize(
    ("max_requests", "window_seconds"),
    [(0, 60), (-1, 60), (5, 0), (5, -10)],
)
def test_invalid_construction_parameters_raise(
    max_requests: int, window_seconds: int
) -> None:
    """Given parametros no positivos, When se construye, Then ValueError."""
    with pytest.raises(ValueError):
        InMemoryRateLimiter(max_requests=max_requests, window_seconds=window_seconds)


@pytest.mark.parametrize(
    ("client_host", "expected"),
    [("1.2.3.4", "1.2.3.4"), ("  5.6.7.8 ", "5.6.7.8"), (None, "unknown"), ("", "unknown")],
)
def test_client_key_from_host(client_host: str | None, expected: str) -> None:
    """Given una IP (o su ausencia), When se deriva la clave, Then es estable."""
    assert client_key_from_host(client_host) == expected


def test_build_rate_limit_key_combines_scope_and_client() -> None:
    """Given ambito y cliente, When se construye la clave, Then se combinan."""
    assert build_rate_limit_key("auth.route", "1.2.3.4") == "auth.route:1.2.3.4"
    assert build_rate_limit_key("auth.session", None) == "auth.session:unknown"


# --------------------------------------------------------------------------- #
# Nivel 2: aplicacion del refuerzo en los endpoints
# --------------------------------------------------------------------------- #


def _build_test_app(limiter: InMemoryRateLimiter) -> FastAPI:
    """Monta un arnes minimo con el limitador en `app.state` y el auth_router.

    Replica el cableado relevante de `create_app` (middleware de correlacion,
    manejadores de error, limitador en estado) sin invocar `StartupValidator`,
    de modo que las pruebas no dependan de variables de entorno.

    :param limiter: limitador compartido a exponer en ``app.state``.
    :return: la aplicacion FastAPI de prueba.
    """
    app = FastAPI()
    app.state.rate_limiter = limiter
    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app)
    api_v1_router = APIRouter(prefix=API_V1_PREFIX)
    api_v1_router.include_router(auth_router)
    app.include_router(api_v1_router)
    return app


@pytest.fixture()
def single_request_limiter() -> InMemoryRateLimiter:
    """Limitador con cupo de 1 solicitud por ventana para forzar el 429.

    :return: un `InMemoryRateLimiter` con ``max_requests=1``.
    """
    return InMemoryRateLimiter(max_requests=1, window_seconds=300)


@pytest.fixture()
def client(single_request_limiter: InMemoryRateLimiter) -> Iterator[TestClient]:
    """Cliente de prueba con el limitador de cupo 1 montado en la app.

    :param single_request_limiter: limitador compartido con cupo de 1.
    :yield: `TestClient` listo para ejercitar los endpoints de auth.
    """
    yield TestClient(_build_test_app(single_request_limiter))


def test_route_second_request_is_rate_limited(client: TestClient) -> None:
    """Given cupo agotado, When se repite /route, Then 429 generico con Retry-After."""
    # Given: la primera solicitud consume el unico cupo disponible.
    first = client.post(ROUTE_PATH, json={"identifier": _VALID_EMAIL})
    assert first.status_code == 200

    # When: la segunda solicitud supera el limite.
    second = client.post(ROUTE_PATH, json={"identifier": _VALID_EMAIL})

    # Then: 429 generico con cuerpo `ErrorResponse` y cabecera Retry-After.
    assert second.status_code == 429
    body = second.json()
    assert set(body.keys()) == {"detail", "correlation_id"}
    assert body["correlation_id"]
    assert second.headers[CORRELATION_ID_HEADER] == body["correlation_id"]
    retry_after = second.headers.get("retry-after")
    assert retry_after is not None and int(retry_after) > 0
    # No se filtra el limite ni la ventana configurados.
    assert "1" not in body["detail"] or "limit" not in body["detail"].lower()


def test_session_second_request_is_rate_limited_before_token_verification(
    client: TestClient,
) -> None:
    """Given cupo agotado, When se repite /session, Then 429 sin verificar token."""
    # Given: el Admin SDK mockeado; la primera solicitud consume el cupo.
    with patch(
        "auth.router.get_session_cookie_name", return_value="session"
    ), patch(
        "auth.router.get_session_cookie_max_age_seconds", return_value=3600
    ), patch(
        "auth.router.verify_id_token", return_value={"uid": "u1"}
    ) as mock_verify, patch(
        "auth.router.create_session_cookie", return_value="cookie.value"
    ):
        first = client.post(SESSION_PATH, json={"id_token": _SAMPLE_ID_TOKEN})
        assert first.status_code == 204
        assert mock_verify.call_count == 1

        # When: la segunda solicitud supera el limite.
        second = client.post(SESSION_PATH, json={"id_token": _SAMPLE_ID_TOKEN})

    # Then: 429 generico y el token NO se vuelve a verificar (refuerzo previo).
    assert second.status_code == 429
    assert set(second.json().keys()) == {"detail", "correlation_id"}
    assert int(second.headers["retry-after"]) > 0
    assert mock_verify.call_count == 1
    # No se emite Set-Cookie en el rechazo por rate limiting.
    assert "set-cookie" not in {k.lower() for k in second.headers.keys()}


def test_scopes_do_not_share_quota(client: TestClient) -> None:
    """Given cupo de /route agotado, When se llama /session, Then no se bloquea."""
    # Given: se agota el cupo del ambito de enrutamiento.
    assert client.post(ROUTE_PATH, json={"identifier": _VALID_EMAIL}).status_code == 200
    assert client.post(ROUTE_PATH, json={"identifier": _VALID_EMAIL}).status_code == 429

    # When / Then: el ambito de sesion mantiene su propio cupo independiente.
    with patch(
        "auth.router.get_session_cookie_name", return_value="session"
    ), patch(
        "auth.router.get_session_cookie_max_age_seconds", return_value=3600
    ), patch(
        "auth.router.verify_id_token", return_value={"uid": "u1"}
    ), patch(
        "auth.router.create_session_cookie", return_value="cookie.value"
    ):
        session_response = client.post(
            SESSION_PATH, json={"id_token": _SAMPLE_ID_TOKEN}
        )
    assert session_response.status_code == 204
