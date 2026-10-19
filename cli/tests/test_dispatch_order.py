"""Pruebas del orden de ejecución del dispatch de la CLI_Gestion (R7.7, Property 12).

El invariante central de la tarea 13.1 es: ante parámetros inválidos, incompletos
o con claves no esperadas, la CLI SHALL abortar ANTES de resolver o inicializar
credenciales y, por tanto, antes de cualquier llamada de modificación al Admin SDK.

Para comprobarlo sin consumir Firebase real, se parchea ``resolve_credentials_path``
e ``initialize_firebase_app`` con espías que fallan si llegan a ser invocadas; si la
validación aborta primero, los espías nunca se ejecutan.
"""

from __future__ import annotations

import pytest

from cli import main as cli_main
from cli.errors import ArgumentValidationError


def _fallar_si_se_invoca(*_args: object, **_kwargs: object) -> object:
    """Espía que marca una violación del invariante si llega a invocarse."""
    raise AssertionError(
        "Se resolvieron/inicializaron credenciales pese a parámetros inválidos"
    )


@pytest.fixture(autouse=True)
def _bloquear_credenciales(monkeypatch: pytest.MonkeyPatch) -> None:
    """Impide que la resolución/inicialización de credenciales ocurra en estas pruebas.

    También bloquea la obtención del cliente del tenant: ninguna de estas pruebas
    debe llegar a tocar el Admin SDK, pues validan que el aborto ocurre antes.
    """
    monkeypatch.setattr(cli_main, "resolve_credentials_path", _fallar_si_se_invoca)
    monkeypatch.setattr(cli_main, "initialize_firebase_app", _fallar_si_se_invoca)
    monkeypatch.setattr(cli_main, "auth_client_for_tenant", _fallar_si_se_invoca)


def test_crear_sin_email_aborta_antes_de_credenciales() -> None:
    # Given un comando de alta al que le falta --email (argparse exige el flag)
    # When / Then argparse aborta (SystemExit) sin tocar credenciales
    with pytest.raises(SystemExit):
        cli_main._dispatch(["crear_usuario"], command="crear_usuario")


def test_email_demasiado_corto_aborta_identificando_parametro() -> None:
    # Given un correo que no cumple la longitud mínima
    # When se despacha
    with pytest.raises(ArgumentValidationError) as exc_info:
        cli_main._dispatch(
            ["crear_usuario", "--email", "a@b"], command="crear_usuario"
        )
    # Then el error identifica el parámetro problemático y no se tocaron credenciales
    assert "email" in str(exc_info.value)


def test_clave_no_esperada_aborta_antes_de_credenciales() -> None:
    # Given un flag desconocido para el comando de alta
    # When / Then argparse rechaza el flag desconocido con SystemExit
    with pytest.raises(SystemExit):
        cli_main._dispatch(
            ["crear_usuario", "--email", "cliente@test.com", "--extra", "x"],
            command="crear_usuario",
        )


def test_actualizar_sin_uid_aborta_antes_de_credenciales() -> None:
    # Given actualizar sin el --uid obligatorio
    # When / Then aborta sin resolver credenciales
    with pytest.raises(SystemExit):
        cli_main._dispatch(["actualizar_usuario"], command="actualizar_usuario")


def test_params_validos_pasan_a_resolver_credenciales(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given parámetros válidos y un tenant configurado (para que la resolución del
    # tenant no aborte antes); el espía de credenciales se dispara al ser alcanzado,
    # confirmando que la validación pasó y el flujo avanzó hacia las credenciales.
    monkeypatch.setenv("FIREBASE_TENANT_ID", "poc-auth-2fa-vh5h3")
    with pytest.raises(AssertionError):
        cli_main._dispatch(
            ["crear_usuario", "--email", "cliente@test.com"],
            command="crear_usuario",
        )


def test_tenant_ausente_aborta_antes_de_resolver_credenciales(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given parámetros válidos pero ningún tenant configurado (sin env ni flag)
    monkeypatch.delenv("FIREBASE_TENANT_ID", raising=False)

    # When / Then se aborta por falta de tenant ANTES de tocar credenciales (los
    # espías de credenciales fallarían con AssertionError si se alcanzaran)
    with pytest.raises(ArgumentValidationError) as exc_info:
        cli_main._dispatch(
            ["crear_usuario", "--email", "cliente@test.com"],
            command="crear_usuario",
        )
    assert "tenant" in str(exc_info.value).lower()
