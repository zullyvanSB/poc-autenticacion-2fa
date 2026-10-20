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
- Allowlist de dominios corporativos (`get_corporate_allowed_domains`): parseo
  de lista separada por comas, normalizacion a minusculas, fail-safe a lista
  vacia y semantica de match exacto (sin subdominios). Incluye property-based
  testing (hypothesis) de las invariantes del getter.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st
import pytest

from common.config import (
    get_app_env,
    get_corporate_allowed_domains,
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


# --- Allowlist de dominios corporativos: get_corporate_allowed_domains ---------
#
# El getter lee la variable opcional CORPORATE_ALLOWED_DOMAINS (lista separada
# por comas), recorta espacios, descarta vacias y normaliza a minusculas; es
# fail-safe a lista vacia. Solo *provee* la lista: la semantica de match exacto
# (sin subdominios) la aplica el consumidor (POCA-004). Todos los casos inyectan
# un `env` explicito; nunca se lee ni muta `os.environ` real.

_CORP_VAR = "CORPORATE_ALLOWED_DOMAINS"


def test_corporate_domains_valid_list_is_parsed_and_normalized() -> None:
    """Given dos dominios validos, When se parsean, Then lista normalizada de dos dominios."""
    # Given: allowlist con los dos dominios corporativos permitidos (AC-6).
    env = {_CORP_VAR: "segurosbolivar.com,serviciosbolivar.com"}
    # When
    domains = get_corporate_allowed_domains(env)
    # Then: ambos permitidos, en orden; samtel.eu NO aparece.
    assert domains == ["segurosbolivar.com", "serviciosbolivar.com"]
    assert "samtel.eu" not in domains


def test_corporate_domains_absent_returns_empty_list() -> None:
    """Given la variable ausente, When se parsea, Then lista vacia (fail-safe, AC-5)."""
    # Given: entorno sin CORPORATE_ALLOWED_DOMAINS.
    env: dict[str, str] = {}
    # When/Then: fail-safe, nunca dominios hardcodeados como default.
    assert get_corporate_allowed_domains(env) == []


@pytest.mark.parametrize("raw", ["", "   ", ",", " , , ", ",,,"])
def test_corporate_domains_empty_or_separators_only_returns_empty(raw: str) -> None:
    """Given valor vacio o solo comas/espacios, When se parsea, Then lista vacia."""
    # Given: variable presente pero sin dominios efectivos.
    env = {_CORP_VAR: raw}
    # When/Then
    assert get_corporate_allowed_domains(env) == []


def test_corporate_domains_trimmed_and_lowercased() -> None:
    """Given espacios y mayusculas, When se parsea, Then entradas recortadas y en minusculas."""
    # Given: entradas con espacios y casing mixto.
    env = {_CORP_VAR: " SegurosBolivar.COM , serviciosbolivar.com "}
    # When
    domains = get_corporate_allowed_domains(env)
    # Then: recortadas y normalizadas a minusculas.
    assert domains == ["segurosbolivar.com", "serviciosbolivar.com"]


def test_corporate_domains_exact_match_not_subdomain() -> None:
    """Given un subdominio en la lista, When se parsea, Then se conserva como entrada aparte."""
    # Given: subdominio configurado explicitamente junto al dominio base.
    env = {_CORP_VAR: "mail.segurosbolivar.com,segurosbolivar.com"}
    # When
    domains = get_corporate_allowed_domains(env)
    # Then: el subdominio es una entrada independiente; no colapsa al dominio base (FR-6).
    assert "mail.segurosbolivar.com" in domains
    assert "segurosbolivar.com" in domains
    # El getter no aplica semantica de sufijo: un subdominio solo esta si se configuro.
    env_base_only = {_CORP_VAR: "segurosbolivar.com"}
    assert "mail.segurosbolivar.com" not in get_corporate_allowed_domains(env_base_only)


def test_corporate_domains_samtel_absent_unless_configured() -> None:
    """Given allowlist sin samtel.eu, When se parsea, Then samtel.eu NO esta permitido (AC-6)."""
    # Given: allowlist aprobada (solo los dos dominios corporativos).
    env = {_CORP_VAR: "segurosbolivar.com,serviciosbolivar.com"}
    # When/Then
    assert "samtel.eu" not in get_corporate_allowed_domains(env)


# --- Property-based testing (hypothesis) de las invariantes del getter ---------

# Tokens de dominio simples sin comas ni espacios (una "entrada" valida tras el
# recorte). Se excluyen la coma (separador) y los espacios (recorte) para razonar
# sobre entradas atomicas; el casing se explora por separado.
_domain_token = st.text(
    alphabet=st.characters(blacklist_characters=", \t\n\r", min_codepoint=33, max_codepoint=126),
    min_size=1,
    max_size=20,
)


@given(st.lists(_domain_token, min_size=1, max_size=5))
def test_pbt_normalization_is_idempotent(tokens: list[str]) -> None:
    """Parsear una lista ya normalizada devuelve la misma lista (idempotencia)."""
    # Given: una allowlist cruda.
    raw = ",".join(tokens)
    once = get_corporate_allowed_domains({_CORP_VAR: raw})
    # When: se re-parsea el resultado ya normalizado.
    twice = get_corporate_allowed_domains({_CORP_VAR: ",".join(once)})
    # Then: normalizar es idempotente.
    assert once == twice


@given(st.lists(_domain_token, min_size=1, max_size=5))
def test_pbt_whitespace_around_entries_is_invariant(tokens: list[str]) -> None:
    """Insertar espacios alrededor de cada entrada no cambia el resultado."""
    # Given: la misma lista con y sin espacios alrededor de cada token.
    raw = ",".join(tokens)
    padded = ",".join(f"  {t}  " for t in tokens)
    # When/Then
    assert get_corporate_allowed_domains({_CORP_VAR: padded}) == get_corporate_allowed_domains(
        {_CORP_VAR: raw}
    )


@given(st.lists(_domain_token, min_size=1, max_size=5))
def test_pbt_result_is_always_lowercase(tokens: list[str]) -> None:
    """Para cualquier casing, el resultado esta siempre en minusculas (case-insensitive)."""
    # Given: una allowlist con casing arbitrario.
    raw = ",".join(tokens)
    # When
    domains = get_corporate_allowed_domains({_CORP_VAR: raw})
    # Then: ninguna entrada difiere de su version en minusculas.
    assert all(d == d.lower() for d in domains)


@given(st.text(alphabet=st.sampled_from([",", " ", "\t"]), min_size=0, max_size=10))
def test_pbt_only_separators_is_failsafe(raw: str) -> None:
    """Cualquier cadena de solo comas/espacios produce lista vacia (fail-safe)."""
    # Given/When/Then
    assert get_corporate_allowed_domains({_CORP_VAR: raw}) == []


@given(st.lists(_domain_token, min_size=0, max_size=5))
def test_pbt_never_introduces_empty_entries(tokens: list[str]) -> None:
    """Ninguna entrada del resultado es cadena vacia."""
    # Given: una allowlist posiblemente vacia.
    raw = ",".join(tokens)
    # When
    domains = get_corporate_allowed_domains({_CORP_VAR: raw})
    # Then
    assert all(d != "" for d in domains)
