"""Pruebas unitarias y PBT de la funcion pura `classify_route` (POCA-002).

Cubren la tercera rama de clasificacion anadida en este work item: un correo
cuyo dominio pertenece a la allowlist corporativa (match exacto, case-insensitive)
resuelve `UserType.CORPORATIVO` / ``login_method = "google_redirect"``, evaluado
**antes** del catch-all de correo cliente, sin regresion de los caminos A (cedula
-> Interno) ni C (correo no corporativo -> Cliente).

La allowlist se **inyecta** por parametro (`allowed_corporate_domains=[...]`); nunca
se lee ni se muta `os.environ`. `routing.py` es logica pura: no hay fronteras
externas que simular mas alla de la allowlist inyectada (configuracion).

Cobertura (Given/When/Then), trazada a los AC de `spec.md`:
- AC-1/AC-2: correos `@segurosbolivar.com` / `@serviciosbolivar.com` -> CORPORATIVO.
- AC-3: correo de dominio no corporativo -> CLIENTE (no regresion del camino C).
- AC-4: orden corporativo-antes-del-catch-all.
- AC-5: match exacto (subdominio y `@samtel.eu` -> CLIENTE).
- FR-6: case-insensitive del dominio.
- FR-9: allowlist vacia -> fail-safe a CLIENTE.
- FR-8: identificador no clasificable -> error sin el valor.
Ademas PBT (hypothesis) de las invariantes del plan.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from hypothesis import given
from hypothesis import strategies as st

from auth.routing import (
    LOGIN_METHOD_CLIENTE,
    LOGIN_METHOD_CORPORATIVO,
    LOGIN_METHOD_INTERNO,
    UnclassifiableIdentifierError,
    classify_route,
)
from common.models import UserType

# Allowlist inyectada en los tests. Nunca se lee ni se muta `os.environ`: la
# frontera de configuracion se simula pasando la lista directamente.
_ALLOWLIST = ["segurosbolivar.com", "serviciosbolivar.com"]


def test_classify_corporativo_segurosbolivar() -> None:
    """Given un correo @segurosbolivar.com, When se clasifica, Then CORPORATIVO/google_redirect (AC-1)."""
    # Given / When
    result = classify_route(
        "persona@segurosbolivar.com", allowed_corporate_domains=_ALLOWLIST
    )
    # Then
    assert result.route_type is UserType.CORPORATIVO
    assert result.login_method == "google_redirect"


def test_classify_corporativo_serviciosbolivar() -> None:
    """Given un correo @serviciosbolivar.com, When se clasifica, Then CORPORATIVO/google_redirect (AC-2)."""
    # Given / When
    result = classify_route(
        "persona@serviciosbolivar.com", allowed_corporate_domains=_ALLOWLIST
    )
    # Then
    assert result.route_type is UserType.CORPORATIVO
    assert result.login_method == LOGIN_METHOD_CORPORATIVO


def test_classify_cliente_dominio_no_corporativo() -> None:
    """Given un correo @gmail.com, When se clasifica, Then CLIENTE/password_totp (AC-3, no regresion)."""
    # Given / When
    result = classify_route("persona@gmail.com", allowed_corporate_domains=_ALLOWLIST)
    # Then: camino C intacto.
    assert result.route_type is UserType.CLIENTE
    assert result.login_method == LOGIN_METHOD_CLIENTE


def test_classify_interno_cedula() -> None:
    """Given una cedula de solo digitos, When se clasifica, Then INTERNO/saml_redirect (camino A)."""
    # Given / When: la allowlist no debe afectar el camino de cedula.
    result = classify_route("1234567", allowed_corporate_domains=_ALLOWLIST)
    # Then: camino A intacto.
    assert result.route_type is UserType.INTERNO
    assert result.login_method == LOGIN_METHOD_INTERNO


def test_classify_orden_corporativo_antes_de_cliente() -> None:
    """Given un correo corporativo y allowlist poblada, When se clasifica, Then nunca CLIENTE (AC-4)."""
    # Given: un correo que tambien es un correo valido (catch-all cliente lo
    # aceptaria). When se clasifica con la allowlist poblada.
    result = classify_route(
        "empleado@serviciosbolivar.com", allowed_corporate_domains=_ALLOWLIST
    )
    # Then: la rama corporativa gana al catch-all; el resultado no es CLIENTE.
    assert result.route_type is UserType.CORPORATIVO
    assert result.route_type is not UserType.CLIENTE


def test_classify_match_exacto_subdominio_y_samtel() -> None:
    """Given subdominio y @samtel.eu, When se clasifica, Then CLIENTE (AC-5, match exacto)."""
    # Given / When: un subdominio de un dominio permitido NO hace match exacto.
    subdominio = classify_route(
        "user@mail.segurosbolivar.com", allowed_corporate_domains=_ALLOWLIST
    )
    # And: un dominio fuera de la allowlist aprobada tampoco.
    samtel = classify_route("user@samtel.eu", allowed_corporate_domains=_ALLOWLIST)
    # Then: ambos caen en el camino C.
    assert subdominio.route_type is UserType.CLIENTE
    assert samtel.route_type is UserType.CLIENTE


def test_classify_dominio_case_insensitive() -> None:
    """Given un correo corporativo con mayusculas, When se clasifica, Then CORPORATIVO (FR-6)."""
    # Given / When: el dominio se compara en minusculas.
    result = classify_route(
        "User@SegurosBolivar.COM", allowed_corporate_domains=_ALLOWLIST
    )
    # Then
    assert result.route_type is UserType.CORPORATIVO
    assert result.login_method == LOGIN_METHOD_CORPORATIVO


def test_classify_allowlist_vacia_resuelve_cliente() -> None:
    """Given allowlist vacia, When se clasifica un correo corporativo, Then CLIENTE (fail-safe, FR-9)."""
    # Given / When: sin dominios configurados, ningun correo es corporativo.
    result = classify_route("persona@segurosbolivar.com", allowed_corporate_domains=[])
    # Then: fail-safe al camino C.
    assert result.route_type is UserType.CLIENTE
    assert result.login_method == LOGIN_METHOD_CLIENTE


def test_classify_no_clasificable_eleva_error() -> None:
    """Given un identificador no clasificable, When se clasifica, Then error sin el valor (FR-8)."""
    # Given: ni correo valido ni cedula (patron numerico falla).
    identifier = "abcdef"
    # When / Then: eleva el error de dominio.
    try:
        classify_route(identifier, allowed_corporate_domains=_ALLOWLIST)
    except UnclassifiableIdentifierError as exc:
        # El mensaje del error no transporta el identificador (leakage prevention).
        assert identifier not in str(exc)
    else:  # pragma: no cover - el camino de exito no debe ocurrir
        raise AssertionError("se esperaba UnclassifiableIdentifierError")


def test_classify_digitos_fuera_de_rango_no_clasificable() -> None:
    """Given una cadena de digitos fuera del rango de cedula, Then error (camino A, guarda de longitud)."""
    # Given: 5 caracteres (pasa min_length de RouteRequest) pero no es correo ni
    # cedula valida porque "abcd1" no es solo digitos; usar digitos cortos no
    # aplica (min es 5). Se usa una cadena de digitos demasiado larga (>15).
    identifier = "0123456789012345"  # 16 digitos: excede el maximo de cedula (15)
    # When / Then: ni correo ni cedula de longitud valida -> no clasificable.
    try:
        classify_route(identifier, allowed_corporate_domains=_ALLOWLIST)
    except UnclassifiableIdentifierError as exc:
        assert identifier not in str(exc)
    else:  # pragma: no cover - el camino de exito no debe ocurrir
        raise AssertionError("se esperaba UnclassifiableIdentifierError")


# --------------------------------------------------------------------------- #
# Property-based testing (hypothesis) — invariantes del plan.                 #
# --------------------------------------------------------------------------- #

# Etiqueta de dominio simple (sin punto): letras/digitos con guiones internos.
_DOMAIN_LABEL = st.from_regex(
    re.compile(r"\A[a-z]([a-z0-9]{0,8})\Z"), fullmatch=True
)
# Parte local simple de un correo valido para `_is_valid_email`.
_LOCAL_PART = st.from_regex(re.compile(r"\A[a-z][a-z0-9]{0,8}\Z"), fullmatch=True)


@st.composite
def _non_corporate_emails(draw: st.DrawFn) -> str:
    """Genera correos validos cuyo dominio NO esta en la allowlist."""
    local = draw(_LOCAL_PART)
    label = draw(_DOMAIN_LABEL)
    tld = draw(st.sampled_from(["net", "org", "io", "co"]))
    domain = f"{label}.{tld}"
    # Garantiza que el dominio no coincide con la allowlist.
    assume_domain = domain not in _ALLOWLIST
    assert assume_domain  # los dominios generados nunca igualan la allowlist
    return f"{local}@{domain}"


@given(email=_non_corporate_emails())
def test_pbt_no_regresion_cliente(email: str) -> None:
    """Propiedad: correo valido de dominio fuera de la allowlist -> siempre CLIENTE."""
    result = classify_route(email, allowed_corporate_domains=_ALLOWLIST)
    assert result.route_type is UserType.CLIENTE
    assert result.login_method == LOGIN_METHOD_CLIENTE


@given(digits=st.text(alphabet="0123456789", min_size=5, max_size=15))
def test_pbt_no_regresion_interno(digits: str) -> None:
    """Propiedad: cadena de solo digitos (5-15) -> INTERNO, independiente de la allowlist."""
    result = classify_route(digits, allowed_corporate_domains=_ALLOWLIST)
    assert result.route_type is UserType.INTERNO
    assert result.login_method == LOGIN_METHOD_INTERNO


@given(local=_LOCAL_PART, domain=st.sampled_from(_ALLOWLIST))
def test_pbt_precedencia_corporativa(local: str, domain: str) -> None:
    """Propiedad: correo de dominio en la allowlist -> CORPORATIVO, nunca CLIENTE."""
    result = classify_route(f"{local}@{domain}", allowed_corporate_domains=_ALLOWLIST)
    assert result.route_type is UserType.CORPORATIVO
    assert result.route_type is not UserType.CLIENTE


@given(
    local=_LOCAL_PART,
    domain=st.sampled_from(_ALLOWLIST),
    flips=st.lists(st.booleans(), min_size=1),
)
def test_pbt_case_insensitive(local: str, domain: str, flips: list[bool]) -> None:
    """Propiedad: variar el casing del dominio no cambia la clasificacion corporativa."""
    scrambled = "".join(
        ch.upper() if flip else ch
        for ch, flip in zip(domain, flips * len(domain))
    )
    result = classify_route(
        f"{local}@{scrambled}", allowed_corporate_domains=_ALLOWLIST
    )
    assert result.route_type is UserType.CORPORATIVO


@given(local=_LOCAL_PART, sub=_DOMAIN_LABEL, domain=st.sampled_from(_ALLOWLIST))
def test_pbt_match_exacto(local: str, sub: str, domain: str) -> None:
    """Propiedad: un subdominio de un dominio permitido NO resuelve CORPORATIVO."""
    result = classify_route(
        f"{local}@{sub}.{domain}", allowed_corporate_domains=_ALLOWLIST
    )
    assert result.route_type is UserType.CLIENTE


@given(
    local=_LOCAL_PART,
    domain=st.sampled_from(_ALLOWLIST + ["gmail.com", "mail.segurosbolivar.com"]),
)
def test_pbt_determinismo(local: str, domain: str) -> None:
    """Propiedad: misma entrada + misma allowlist -> mismo resultado (pureza)."""
    allowlist: Sequence[str] = _ALLOWLIST
    email = f"{local}@{domain}"
    first = classify_route(email, allowed_corporate_domains=allowlist)
    second = classify_route(email, allowed_corporate_domains=allowlist)
    assert first == second
