"""Pruebas unitarias de la configuracion por entorno (`APP_ENV`) en config.py.

Verifican los accesores que derivan el comportamiento segun el entorno de
ejecucion a partir de la variable opcional ``APP_ENV`` y de
``CORS_ALLOWED_ORIGINS``, sin consumir recursos externos ni variables reales
(se inyecta un mapeo de entorno explicito en cada caso).

Cobertura (Given/When/Then):
- Produccion por defecto (fail-safe): cookie `Secure` + `SameSite=strict` y
  sin origenes CORS cuando `APP_ENV` esta ausente, vacio o es desconocido.
- Local (`APP_ENV=local`): cookie no-`Secure` + `SameSite=lax` y origenes CORS
  por defecto de localhost:4200.
- `CORS_ALLOWED_ORIGINS` explicito: se respetan los origenes dados y se
  descartan entradas vacias.
"""

from __future__ import annotations

import pytest

from common.config import (
    get_app_env,
    get_cors_allowed_origins,
    get_session_cookie_samesite,
    get_session_cookie_secure,
    is_local_env,
)


@pytest.mark.parametrize(
    "env",
    [
        {},  # APP_ENV ausente
        {"APP_ENV": ""},  # vacio
        {"APP_ENV": "   "},  # solo espacios
        {"APP_ENV": "production"},  # explicito
        {"APP_ENV": "staging"},  # desconocido => production (fail-safe)
        {"APP_ENV": "LOCALHOST"},  # no es exactamente "local"
    ],
)
def test_app_env_defaults_to_production(env: dict[str, str]) -> None:
    """Given APP_ENV ausente/desconocido, When se resuelve el entorno, Then es production."""
    # Given/When/Then: cualquier valor que no sea exactamente "local" => production.
    assert get_app_env(env) == "production"
    assert is_local_env(env) is False


@pytest.mark.parametrize("value", ["local", "LOCAL", "  Local  "])
def test_app_env_local_is_detected_case_insensitively(value: str) -> None:
    """Given APP_ENV=local (en cualquier caja), When se resuelve, Then es local."""
    # Given/When/Then
    env = {"APP_ENV": value}
    assert get_app_env(env) == "local"
    assert is_local_env(env) is True


def test_production_cookie_is_secure_and_strict() -> None:
    """Given produccion, When se derivan atributos de cookie, Then Secure + strict."""
    # Given: entorno de produccion (APP_ENV ausente).
    env: dict[str, str] = {}
    # When/Then
    assert get_session_cookie_secure(env) is True
    assert get_session_cookie_samesite(env) == "strict"


def test_local_cookie_is_not_secure_and_lax() -> None:
    """Given local, When se derivan atributos de cookie, Then no-Secure + lax."""
    # Given: entorno local explicito.
    env = {"APP_ENV": "local"}
    # When/Then: relajamiento SOLO en local para el flujo HTTP del dev server.
    assert get_session_cookie_secure(env) is False
    assert get_session_cookie_samesite(env) == "lax"


def test_cors_origins_default_to_localhost_in_local() -> None:
    """Given local sin CORS explicito, When se piden origenes, Then defaults de localhost."""
    # Given: entorno local sin CORS_ALLOWED_ORIGINS.
    env = {"APP_ENV": "local"}
    # When
    origins = get_cors_allowed_origins(env)
    # Then: origenes por defecto del dev server de Angular.
    assert origins == ["http://localhost:4200", "http://127.0.0.1:4200"]


def test_cors_origins_empty_in_production_by_default() -> None:
    """Given produccion sin CORS explicito, When se piden origenes, Then lista vacia."""
    # Given: entorno de produccion (APP_ENV ausente), sin CORS_ALLOWED_ORIGINS.
    env: dict[str, str] = {}
    # When/Then: sin CORS por defecto (fail-safe).
    assert get_cors_allowed_origins(env) == []


def test_cors_origins_explicit_are_trimmed_and_respected() -> None:
    """Given CORS explicito, When se piden origenes, Then se respetan y se limpian vacios."""
    # Given: lista separada por comas con espacios y entradas vacias.
    env = {
        "APP_ENV": "production",
        "CORS_ALLOWED_ORIGINS": " https://app.example.com , ,https://admin.example.com ",
    }
    # When
    origins = get_cors_allowed_origins(env)
    # Then: entradas recortadas, sin vacios, en orden.
    assert origins == ["https://app.example.com", "https://admin.example.com"]
