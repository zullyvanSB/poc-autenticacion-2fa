"""Pruebas unitarias de la verificacion de ID token y derivacion de tipo (tarea 6.1).

Cubren `auth.firebase_auth.verify_id_token` y `derive_user_type` con el Firebase
Admin SDK **mockeado** (nunca se consume un proveedor real; R9.1 aplica a los
tests de integracion, no a estos unitarios). Se verifica:

- Verificacion exitosa devuelve los claims decodificados y delega en
  ``auth.verify_id_token`` con tolerancia de reloj de 60 s (R5.3).
- Cualquier fallo del Admin SDK (``ValueError`` o ``FirebaseError`` y sus
  subclases: token invalido, expirado, fallo de certificados) se traduce a
  `InvalidIdTokenError` de dominio con mensaje generico y sin filtrar el token
  ni la causa concreta al cliente (R5.5, R10.4; Property 4).
- Derivacion de `UserType`: ``password`` -> Cliente; ``saml.<id>`` -> Interno;
  proveedor desconocido o ausente -> `UnknownSignInProviderError` (R4.6, R4.7).

Se evita invocar `_ensure_initialized` real: se mockea para no requerir
credenciales ni red (Given/When/Then; sin mocks de datos que falseen la logica).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from firebase_admin import exceptions as firebase_exceptions
from firebase_admin.auth import ExpiredIdTokenError, InvalidIdTokenError as SdkInvalidIdTokenError

from auth.firebase_auth import (
    CLOCK_SKEW_SECONDS,
    FirebaseAuthError,
    InvalidIdTokenError,
    InvalidSessionCookieError,
    UnknownSignInProviderError,
    _SIGN_IN_PROVIDER_CLAIM,
    _TENANT_CLAIM,
    derive_user_type,
    verify_id_token,
    verify_session_cookie,
)
from common.models import UserType

# Token de ejemplo (no es un JWT real; el Admin SDK esta mockeado). Nunca se
# registra ni viaja al cliente en el flujo real.
_SAMPLE_TOKEN = "sample.id.token"

# Tenant esperado para las pruebas. No depende de un valor real: la verificacion
# resuelve el tenant esperado via `get_firebase_tenant_id`, que se parchea en
# cada prueba para no depender de variables de entorno (R5.3).
_EXPECTED_TENANT = "poc-auth-2fa-vh5h3"


def _claims_with_provider(
    provider: str | None, tenant: str | None = _EXPECTED_TENANT
) -> dict[str, Any]:
    """Construye claims minimos con el `sign_in_provider` y el `tenant` indicados.

    :param provider: valor del proveedor de ingreso; ``None`` omite el claim
        anidado para simular un token sin proveedor.
    :param tenant: valor del claim ``firebase.tenant``; ``None`` lo omite para
        simular un token de nivel proyecto o de otro tenant.
    :return: diccionario de claims con la estructura ``{"firebase": {...}}``.
    """
    firebase_claim: dict[str, Any] = {}
    if provider is not None:
        firebase_claim[_SIGN_IN_PROVIDER_CLAIM] = provider
    if tenant is not None:
        firebase_claim[_TENANT_CLAIM] = tenant
    return {"firebase": firebase_claim}


# --------------------------------------------------------------------------- #
# verify_id_token
# --------------------------------------------------------------------------- #


def test_verify_id_token_returns_decoded_claims_on_success() -> None:
    """Verificacion exitosa (tenant correcto) devuelve claims y aplica tolerancia de 60 s."""
    # Given: el Admin SDK verifica el token y devuelve claims con el tenant esperado.
    expected_claims = _claims_with_provider("password")
    with patch("auth.firebase_auth._ensure_initialized"), patch(
        "auth.firebase_auth.get_firebase_tenant_id", return_value=_EXPECTED_TENANT
    ), patch(
        "auth.firebase_auth.auth.verify_id_token", return_value=expected_claims
    ) as mock_verify:
        # When: se verifica el ID token.
        claims = verify_id_token(_SAMPLE_TOKEN)

    # Then: se devuelven los claims y se delego con clock_skew_seconds=60 (R5.3).
    assert claims == expected_claims
    mock_verify.assert_called_once_with(
        _SAMPLE_TOKEN, clock_skew_seconds=CLOCK_SKEW_SECONDS
    )
    assert CLOCK_SKEW_SECONDS == 60


@pytest.mark.parametrize(
    "mismatched_claims",
    [
        _claims_with_provider("password", tenant="otro-tenant"),  # otro tenant
        _claims_with_provider("password", tenant=None),  # nivel proyecto (sin claim)
        {"firebase": "no-es-dict"},  # estructura inesperada
        {},  # sin objeto firebase
    ],
)
def test_verify_id_token_rejects_wrong_tenant_generically(
    mismatched_claims: dict[str, Any],
) -> None:
    """Tenant ausente o distinto al configurado se rechaza con `InvalidIdTokenError` generico (R5.3)."""
    # Given: el Admin SDK verifica el token pero el claim tenant no coincide.
    with patch("auth.firebase_auth._ensure_initialized"), patch(
        "auth.firebase_auth.get_firebase_tenant_id", return_value=_EXPECTED_TENANT
    ), patch(
        "auth.firebase_auth.auth.verify_id_token", return_value=mismatched_claims
    ):
        # When / Then: se eleva el MISMO error de dominio generico que un token invalido.
        with pytest.raises(InvalidIdTokenError) as exc_info:
            verify_id_token(_SAMPLE_TOKEN)

    # Then: el mensaje generico no revela que el motivo fue el tenant (R5.5, R10.4).
    assert str(exc_info.value) == "El token de identidad no es valido."


@pytest.mark.parametrize(
    "sdk_exception",
    [
        ValueError("empty token"),
        SdkInvalidIdTokenError("invalid"),
        ExpiredIdTokenError("expired", cause=None),
        firebase_exceptions.FirebaseError(
            code="unknown", message="cert fetch failed"
        ),
    ],
)
def test_verify_id_token_wraps_sdk_failures_generically(
    sdk_exception: Exception,
) -> None:
    """Todo fallo del Admin SDK se traduce a `InvalidIdTokenError` generico."""
    # Given: el Admin SDK falla con un error de verificacion.
    with patch("auth.firebase_auth._ensure_initialized"), patch(
        "auth.firebase_auth.auth.verify_id_token", side_effect=sdk_exception
    ):
        # When / Then: se eleva el error de dominio generico.
        with pytest.raises(InvalidIdTokenError) as exc_info:
            verify_id_token(_SAMPLE_TOKEN)

    # Then: el mensaje expuesto es generico y no incluye el token ni la causa.
    message = str(exc_info.value)
    assert _SAMPLE_TOKEN not in message
    assert "El token de identidad no es valido." == message
    # La causa original se encadena solo para el registro interno del handler.
    assert exc_info.value.__cause__ is sdk_exception


def test_verify_id_token_propagates_initialization_failure() -> None:
    """Si el Admin SDK no inicializa, se eleva `FirebaseAuthError` generico."""
    # Given: la inicializacion perezosa falla.
    with patch(
        "auth.firebase_auth._ensure_initialized",
        side_effect=FirebaseAuthError("init failed"),
    ):
        # When / Then: el error de dominio se propaga sin tocar verify_id_token.
        with pytest.raises(FirebaseAuthError):
            verify_id_token(_SAMPLE_TOKEN)


# --------------------------------------------------------------------------- #
# derive_user_type
# --------------------------------------------------------------------------- #


def test_derive_user_type_password_is_cliente() -> None:
    """`sign_in_provider == "password"` deriva en `UserType.CLIENTE` (R4.6)."""
    # Given: claims con proveedor password.
    claims = _claims_with_provider("password")
    # When: se deriva el tipo de usuario.
    user_type = derive_user_type(claims)
    # Then: corresponde a Cliente.
    assert user_type is UserType.CLIENTE


@pytest.mark.parametrize("provider", ["saml.f5apm", "saml.corporate", "saml.x"])
def test_derive_user_type_saml_is_interno(provider: str) -> None:
    """`sign_in_provider` con prefijo `saml.` deriva en `UserType.INTERNO`."""
    # Given: claims con proveedor SAML.
    claims = _claims_with_provider(provider)
    # When: se deriva el tipo de usuario.
    user_type = derive_user_type(claims)
    # Then: corresponde a Interno (R4.7).
    assert user_type is UserType.INTERNO


@pytest.mark.parametrize(
    "claims",
    [
        {},  # sin objeto firebase
        {"firebase": {}},  # sin sign_in_provider
        {"firebase": {"sign_in_provider": "google.com"}},  # proveedor no soportado
        {"firebase": {"sign_in_provider": "phone"}},
        {"firebase": "no-es-dict"},  # estructura inesperada
    ],
)
def test_derive_user_type_unknown_provider_raises(claims: dict[str, Any]) -> None:
    """Proveedor ausente o no soportado eleva `UnknownSignInProviderError`."""
    # Given / When / Then: la derivacion rechaza el proveedor desconocido.
    with pytest.raises(UnknownSignInProviderError):
        derive_user_type(claims)


# Session cookie de ejemplo (no es una cookie real; el Admin SDK esta mockeado).
_SAMPLE_SESSION_COOKIE = "sample.session.cookie"


# --------------------------------------------------------------------------- #
# verify_session_cookie — tenant validation
# --------------------------------------------------------------------------- #


def test_verify_session_cookie_returns_claims_on_success_with_correct_tenant() -> None:
    """Verificacion de session cookie exitosa con tenant correcto devuelve claims."""
    # Given: el Admin SDK verifica la cookie y devuelve claims con el tenant esperado.
    expected_claims = _claims_with_provider("password")
    with patch("auth.firebase_auth._ensure_initialized"), patch(
        "auth.firebase_auth.get_firebase_tenant_id", return_value=_EXPECTED_TENANT
    ), patch(
        "auth.firebase_auth.auth.verify_session_cookie", return_value=expected_claims
    ) as mock_verify:
        # When: se verifica la session cookie.
        claims = verify_session_cookie(_SAMPLE_SESSION_COOKIE)

    # Then: se devuelven los claims y se delego con check_revoked=False.
    # Decision PoC: el usuario reside en el tenant de Identity Platform y las
    # session cookies se verifican con el cliente de NIVEL PROYECTO. Con
    # check_revoked=True el SDK buscaria al usuario a nivel proyecto y fallaria
    # con UserNotFoundError; la revocacion la cubre el logout (revoke_session).
    assert claims == expected_claims
    mock_verify.assert_called_once_with(_SAMPLE_SESSION_COOKIE, check_revoked=False)


@pytest.mark.parametrize(
    "mismatched_claims",
    [
        _claims_with_provider("password", tenant="otro-tenant"),  # otro tenant
        _claims_with_provider("password", tenant=None),  # nivel proyecto (sin claim)
        {"firebase": "no-es-dict"},  # estructura inesperada
        {},  # sin objeto firebase
    ],
)
def test_verify_session_cookie_rejects_wrong_tenant_generically(
    mismatched_claims: dict[str, Any],
) -> None:
    """Tenant ausente o distinto al configurado se rechaza con `InvalidSessionCookieError` generico."""
    # Given: el Admin SDK verifica la cookie pero el claim tenant no coincide.
    with patch("auth.firebase_auth._ensure_initialized"), patch(
        "auth.firebase_auth.get_firebase_tenant_id", return_value=_EXPECTED_TENANT
    ), patch(
        "auth.firebase_auth.auth.verify_session_cookie", return_value=mismatched_claims
    ):
        # When / Then: se eleva el MISMO error de dominio generico (R5.3, R6.3, R10.4).
        with pytest.raises(InvalidSessionCookieError) as exc_info:
            verify_session_cookie(_SAMPLE_SESSION_COOKIE)

    # Then: el mensaje generico no revela que el motivo fue el tenant.
    assert str(exc_info.value) == "La sesion no es valida."
