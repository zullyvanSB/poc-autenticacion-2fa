"""Pruebas unitarias del comando ``crear_usuario`` de la CLI_Gestion (R7.1, R7.2).

Verifican el comportamiento de la lógica de alta sin consumir Firebase real: el
Firebase Admin SDK se mockea por completo. Las pruebas comprueban que:

- Con un correo de formato válido inexistente, se invoca ``auth.create_user`` con
  el correo y la app inicializada, y se retorna el ``uid`` creado con éxito (R7.1).
- Ante ``EmailAlreadyExistsError``, la operación aborta con ``OperationError``
  informando la duplicidad y sin crear ni modificar usuario alguno (R7.2).
- Cualquier otro ``FirebaseError`` del SDK se traduce a un ``OperationError``
  genérico sin filtrar la traza interna.
- Un correo con formato inválido se rechaza en la validación de parámetros ANTES
  de invocar el Admin SDK (R7.7 / defensa en profundidad del alta).

El SDK no está instalado en el entorno de pruebas; se inyectan módulos falsos
``firebase_admin.auth`` y ``firebase_admin.exceptions`` en ``sys.modules`` para que
la importación perezosa de ``_crear_usuario`` resuelva a los mocks.
"""

from __future__ import annotations

import sys
import types
from typing import Iterator

import pytest

from cli import main as cli_main
from cli.errors import ArgumentValidationError, OperationError
from cli.models import CrearUsuarioArgs


class _FakeFirebaseError(Exception):
    """Sustituto de ``firebase_admin.exceptions.FirebaseError`` para pruebas."""


class _FakeAlreadyExistsError(_FakeFirebaseError):
    """Sustituto de ``AlreadyExistsError`` (base de ``EmailAlreadyExistsError``)."""


class _FakeEmailAlreadyExistsError(_FakeAlreadyExistsError):
    """Sustituto de ``firebase_admin.auth.EmailAlreadyExistsError``."""


class _FakeUserRecord:
    """``UserRecord`` mínimo: solo expone el ``uid`` que consume la lógica."""

    def __init__(self, uid: str) -> None:
        self.uid = uid


class _FakeTenantAuthClient:
    """``auth.Client`` acotado al tenant: expone ``create_user`` y registra llamadas.

    Las operaciones del tenant NO reciben ``app`` como argumento (el cliente ya
    está acotado al tenant y a la app). Por defecto el alta tiene éxito; las
    pruebas pueden reasignar ``create_user`` para simular errores del SDK.
    """

    def __init__(self) -> None:
        self.llamadas: list[dict[str, object]] = []

    def create_user(self, *, email: str) -> _FakeUserRecord:
        self.llamadas.append({"email": email})
        return _FakeUserRecord(_UID_CREADO)


# UID, correo y tenant de ejemplo (no son PII real; placeholders para la PoC).
_UID_CREADO = "firebase-uid-ejemplo-123"
_CORREO_VALIDO = "cliente@test.com"
_TENANT_ID = "poc-auth-2fa-vh5h3"


@pytest.fixture
def fake_admin_sdk(monkeypatch: pytest.MonkeyPatch) -> Iterator[types.SimpleNamespace]:
    """Inyecta módulos falsos del Admin SDK y expone ganchos para configurarlos.

    Returns:
        Un namespace con ``auth`` (módulo falso con ``create_user`` configurable y
        las clases de error) y ``exceptions`` (con ``FirebaseError``).
    """
    exceptions_mod = types.ModuleType("firebase_admin.exceptions")
    exceptions_mod.FirebaseError = _FakeFirebaseError  # type: ignore[attr-defined]

    auth_mod = types.ModuleType("firebase_admin.auth")
    auth_mod.EmailAlreadyExistsError = _FakeEmailAlreadyExistsError  # type: ignore[attr-defined]

    tenant_client = _FakeTenantAuthClient()

    # El módulo tenant_mgt expone auth_for_tenant(tenant_id, app=...) que retorna
    # el cliente acotado al tenant cuyos métodos CRUD son los que se verifican.
    tenant_mgt_mod = types.ModuleType("firebase_admin.tenant_mgt")
    llamadas_auth_for_tenant: list[dict[str, object]] = []

    def _auth_for_tenant(tenant_id: str, *, app: object) -> "_FakeTenantAuthClient":
        llamadas_auth_for_tenant.append({"tenant_id": tenant_id, "app": app})
        return tenant_client

    tenant_mgt_mod.auth_for_tenant = _auth_for_tenant  # type: ignore[attr-defined]
    tenant_mgt_mod._llamadas = llamadas_auth_for_tenant  # type: ignore[attr-defined]

    paquete = types.ModuleType("firebase_admin")
    paquete.auth = auth_mod  # type: ignore[attr-defined]
    paquete.exceptions = exceptions_mod  # type: ignore[attr-defined]
    paquete.tenant_mgt = tenant_mgt_mod  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "firebase_admin", paquete)
    monkeypatch.setitem(sys.modules, "firebase_admin.auth", auth_mod)
    monkeypatch.setitem(sys.modules, "firebase_admin.exceptions", exceptions_mod)
    monkeypatch.setitem(sys.modules, "firebase_admin.tenant_mgt", tenant_mgt_mod)

    yield types.SimpleNamespace(
        auth=auth_mod,
        exceptions=exceptions_mod,
        tenant_mgt=tenant_mgt_mod,
        tenant_client=tenant_client,
    )


def test_crear_usuario_exitoso_retorna_uid_y_exito(
    fake_admin_sdk: types.SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given parámetros válidos y un cliente de autenticación acotado al tenant
    params = CrearUsuarioArgs(email=_CORREO_VALIDO)
    auth_client = fake_admin_sdk.tenant_client

    # When se ejecuta la lógica de alta
    exit_code = cli_main._crear_usuario(params, auth_client)

    # Then se invoca create_user del cliente del tenant con el correo, y se reporta
    # el uid con éxito (R7.1)
    assert exit_code == 0
    assert auth_client.llamadas == [{"email": _CORREO_VALIDO}]
    salida = capsys.readouterr().out
    assert _UID_CREADO in salida


def test_crear_usuario_correo_duplicado_aborta_informando_duplicidad(
    fake_admin_sdk: types.SimpleNamespace,
) -> None:
    # Given un cliente de tenant que lanza EmailAlreadyExistsError al crear
    def _raise_duplicado(*, email: str) -> None:
        raise _FakeEmailAlreadyExistsError("ya existe")

    fake_admin_sdk.tenant_client.create_user = _raise_duplicado
    params = CrearUsuarioArgs(email=_CORREO_VALIDO)

    # When / Then se aborta con OperationError indicando duplicidad (R7.2)
    with pytest.raises(OperationError) as exc_info:
        cli_main._crear_usuario(params, fake_admin_sdk.tenant_client)
    mensaje = str(exc_info.value)
    assert "existente" in mensaje or "duplic" in mensaje.lower()
    # El exit_code específico de operación permite distinguirlo de validación/credenciales
    assert exc_info.value.exit_code == 4


def test_crear_usuario_error_generico_del_sdk_se_traduce_a_operation_error(
    fake_admin_sdk: types.SimpleNamespace,
) -> None:
    # Given un cliente de tenant que lanza un FirebaseError genérico
    def _raise_generico(*, email: str) -> None:
        raise _FakeFirebaseError("fallo interno con detalle sensible")

    fake_admin_sdk.tenant_client.create_user = _raise_generico
    params = CrearUsuarioArgs(email=_CORREO_VALIDO)

    # When / Then se traduce a OperationError sin filtrar el detalle interno
    with pytest.raises(OperationError) as exc_info:
        cli_main._crear_usuario(params, fake_admin_sdk.tenant_client)
    assert "detalle sensible" not in str(exc_info.value)


def test_main_mapea_exit_code_de_duplicidad(
    fake_admin_sdk: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given credenciales resueltas/inicializadas de forma inocua (mocks) y un
    # tenant configurado por variable de entorno
    monkeypatch.setattr(
        cli_main, "resolve_credentials_path", lambda *_a, **_k: "/tmp/fake.json"
    )
    monkeypatch.setattr(
        cli_main, "initialize_firebase_app", lambda *_a, **_k: object()
    )
    monkeypatch.setenv("FIREBASE_TENANT_ID", _TENANT_ID)

    def _raise_duplicado(*, email: str) -> None:
        raise _FakeEmailAlreadyExistsError("ya existe")

    fake_admin_sdk.tenant_client.create_user = _raise_duplicado

    # When se ejecuta el flujo completo con un correo duplicado
    code = cli_main.main(
        ["crear_usuario", "--email", _CORREO_VALIDO], command="crear_usuario"
    )

    # Then main traduce el OperationError al exit_code específico (4)
    assert code == 4
    # El cliente se obtuvo acotado al tenant resuelto desde la configuración
    assert fake_admin_sdk.tenant_mgt._llamadas[0]["tenant_id"] == _TENANT_ID


def test_correo_formato_invalido_se_rechaza_antes_de_invocar_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given espías de credenciales/SDK que fallan si llegan a invocarse
    def _no_invocar(*_a: object, **_k: object) -> object:
        raise AssertionError("no debió alcanzarse el SDK con un correo inválido")

    monkeypatch.setattr(cli_main, "resolve_credentials_path", _no_invocar)
    monkeypatch.setattr(cli_main, "initialize_firebase_app", _no_invocar)

    # When se despacha un correo con longitud válida pero formato inválido
    with pytest.raises(ArgumentValidationError) as exc_info:
        cli_main._dispatch(
            ["crear_usuario", "--email", "no-es-un-correo"],
            command="crear_usuario",
        )

    # Then el error identifica el parámetro 'email' y no se tocó el SDK
    assert "email" in str(exc_info.value)


def test_modelo_crear_usuario_acepta_correo_rfc5322_valido() -> None:
    # Given / When un correo con formato válido
    params = CrearUsuarioArgs(email="  Cliente.Prueba+tag@sub.dominio.com  ")
    # Then se normaliza (trim) y se acepta
    assert params.email == "Cliente.Prueba+tag@sub.dominio.com"


def test_tenant_ausente_aborta_antes_de_cualquier_mutacion(
    fake_admin_sdk: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given parámetros válidos pero NINGÚN tenant configurado (sin env, sin flag)
    monkeypatch.delenv("FIREBASE_TENANT_ID", raising=False)

    # Las credenciales no deberían resolverse ni el cliente del tenant obtenerse si
    # el tenant falta: ambos espías fallan si llegan a invocarse.
    def _no_invocar(*_a: object, **_k: object) -> object:
        raise AssertionError("no debió alcanzarse el SDK sin tenant configurado")

    monkeypatch.setattr(cli_main, "resolve_credentials_path", _no_invocar)
    monkeypatch.setattr(cli_main, "initialize_firebase_app", _no_invocar)
    monkeypatch.setattr(cli_main, "auth_client_for_tenant", _no_invocar)

    # When se despacha un alta válida sin tenant
    with pytest.raises(ArgumentValidationError) as exc_info:
        cli_main._dispatch(
            ["crear_usuario", "--email", _CORREO_VALIDO], command="crear_usuario"
        )

    # Then el error indica que falta el tenant y no se ejecutó ninguna mutación
    assert "tenant" in str(exc_info.value).lower()
    assert fake_admin_sdk.tenant_client.llamadas == []


def test_flag_tenant_tiene_precedencia_sobre_variable_de_entorno(
    fake_admin_sdk: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given credenciales inocuas y un tenant por env, pero también --tenant provisto
    monkeypatch.setattr(
        cli_main, "resolve_credentials_path", lambda *_a, **_k: "/tmp/fake.json"
    )
    monkeypatch.setattr(
        cli_main, "initialize_firebase_app", lambda *_a, **_k: object()
    )
    monkeypatch.setenv("FIREBASE_TENANT_ID", "tenant-de-entorno")

    # When se despacha un alta válida con --tenant explícito
    code = cli_main.main(
        ["crear_usuario", "--email", _CORREO_VALIDO, "--tenant", _TENANT_ID],
        command="crear_usuario",
    )

    # Then la bandera tiene precedencia: el cliente se acota al tenant de la bandera
    assert code == 0
    assert fake_admin_sdk.tenant_mgt._llamadas[0]["tenant_id"] == _TENANT_ID
