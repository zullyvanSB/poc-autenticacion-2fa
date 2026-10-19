"""Pruebas unitarias de ``actualizar_usuario`` y ``eliminar_usuario`` (R7.3–R7.5).

Verifican el comportamiento de la lógica de actualización y baja sin consumir
Firebase real: el Firebase Admin SDK se mockea por completo. Las pruebas comprueban
que:

- Con un ``uid`` existente y datos válidos, ``actualizar_usuario`` invoca
  ``auth.update_user`` con el ``uid``, la app inicializada y solo los campos
  provistos, y retorna éxito (R7.3).
- Con un ``uid`` existente, ``eliminar_usuario`` invoca ``auth.delete_user`` con el
  ``uid`` y la app inicializada, y retorna éxito (R7.4).
- Ante ``UserNotFoundError`` en cualquiera de los dos comandos, la operación aborta
  con ``OperationError`` informando que el usuario no fue encontrado y sin alterar
  dato alguno (R7.5).
- Cualquier otro ``FirebaseError`` del SDK se traduce a un ``OperationError``
  genérico sin filtrar la traza interna.
- ``actualizar_usuario`` sin ningún campo modificable se aborta como validación de
  parámetros ANTES de invocar el Admin SDK (R7.7).

El SDK no está instalado en el entorno de pruebas; se inyectan módulos falsos
``firebase_admin.auth`` y ``firebase_admin.exceptions`` en ``sys.modules`` para que
la importación perezosa de los comandos resuelva a los mocks.
"""

from __future__ import annotations

import sys
import types
from typing import Iterator

import pytest

from cli import main as cli_main
from cli.errors import ArgumentValidationError, OperationError
from cli.models import ActualizarUsuarioArgs, EliminarUsuarioArgs


class _FakeFirebaseError(Exception):
    """Sustituto de ``firebase_admin.exceptions.FirebaseError`` para pruebas."""


class _FakeNotFoundError(_FakeFirebaseError):
    """Sustituto de ``NotFoundError`` (base de ``UserNotFoundError``)."""


class _FakeUserNotFoundError(_FakeNotFoundError):
    """Sustituto de ``firebase_admin.auth.UserNotFoundError``."""


class _FakeUserRecord:
    """``UserRecord`` mínimo: solo expone el ``uid`` que consume la lógica."""

    def __init__(self, uid: str) -> None:
        self.uid = uid


class _FakeTenantAuthClient:
    """``auth.Client`` acotado al tenant con ``update_user``/``delete_user``.

    Las operaciones del tenant NO reciben ``app`` como argumento (el cliente ya
    está acotado al tenant y a la app). Por defecto ambas tienen éxito y registran
    la llamada; las pruebas pueden reasignar los métodos para simular errores.
    """

    def __init__(self) -> None:
        self.llamadas_update: list[dict[str, object]] = []
        self.llamadas_delete: list[dict[str, object]] = []

    def update_user(self, uid: str, **cambios: str) -> _FakeUserRecord:
        self.llamadas_update.append({"uid": uid, "cambios": cambios})
        return _FakeUserRecord(uid)

    def delete_user(self, uid: str) -> None:
        self.llamadas_delete.append({"uid": uid})


# UID, datos y tenant de ejemplo (no son PII real; placeholders para la PoC).
_UID = "firebase-uid-ejemplo-123"
_CORREO_VALIDO = "cliente@test.com"
_DISPLAY_NAME = "Cliente Prueba"
_TENANT_ID = "poc-auth-2fa-vh5h3"


@pytest.fixture
def fake_admin_sdk(monkeypatch: pytest.MonkeyPatch) -> Iterator[types.SimpleNamespace]:
    """Inyecta módulos falsos del Admin SDK y expone ganchos para configurarlos.

    Returns:
        Un namespace con ``auth`` (módulo falso con ``update_user``/``delete_user``
        configurables y las clases de error) y ``exceptions`` (con
        ``FirebaseError``).
    """
    exceptions_mod = types.ModuleType("firebase_admin.exceptions")
    exceptions_mod.FirebaseError = _FakeFirebaseError  # type: ignore[attr-defined]

    auth_mod = types.ModuleType("firebase_admin.auth")
    auth_mod.UserNotFoundError = _FakeUserNotFoundError  # type: ignore[attr-defined]

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


# --- actualizar_usuario -----------------------------------------------------


def test_actualizar_usuario_exitoso_invoca_update_user_y_retorna_exito(
    fake_admin_sdk: types.SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given un uid existente y datos válidos, con un cliente acotado al tenant
    params = ActualizarUsuarioArgs(
        uid=_UID, email=_CORREO_VALIDO, display_name=_DISPLAY_NAME
    )
    auth_client = fake_admin_sdk.tenant_client

    # When se ejecuta la lógica de actualización
    exit_code = cli_main._actualizar_usuario(params, auth_client)

    # Then se invoca update_user del cliente del tenant con el uid y solo los campos
    # provistos, y se reporta éxito (R7.3)
    assert exit_code == 0
    assert auth_client.llamadas_update == [
        {
            "uid": _UID,
            "cambios": {"email": _CORREO_VALIDO, "display_name": _DISPLAY_NAME},
        }
    ]
    assert "actualizado" in capsys.readouterr().out.lower()


def test_actualizar_usuario_envia_solo_los_campos_provistos(
    fake_admin_sdk: types.SimpleNamespace,
) -> None:
    # Given un uid y solo un campo modificable (display_name)
    params = ActualizarUsuarioArgs(uid=_UID, display_name=_DISPLAY_NAME)

    # When se ejecuta la actualización
    exit_code = cli_main._actualizar_usuario(params, fake_admin_sdk.tenant_client)

    # Then solo se envía ese campo al SDK (no se sobrescribe email con None)
    assert exit_code == 0
    assert fake_admin_sdk.tenant_client.llamadas_update[0]["cambios"] == {
        "display_name": _DISPLAY_NAME
    }


def test_actualizar_usuario_sin_campos_aborta_antes_de_invocar_sdk(
    fake_admin_sdk: types.SimpleNamespace,
) -> None:
    # Given un uid válido pero ningún campo modificable
    params = ActualizarUsuarioArgs(uid=_UID)

    # When / Then se aborta en validación (R7.7) sin tocar el SDK
    with pytest.raises(ArgumentValidationError):
        cli_main._actualizar_usuario(params, fake_admin_sdk.tenant_client)
    assert fake_admin_sdk.tenant_client.llamadas_update == []


def test_actualizar_usuario_email_verified_se_envia_al_sdk(
    fake_admin_sdk: types.SimpleNamespace,
) -> None:
    # Given un uid y solo email_verified=True (requisito para enrolar TOTP/MFA)
    params = ActualizarUsuarioArgs(uid=_UID, email_verified=True)

    # When se ejecuta la actualización
    exit_code = cli_main._actualizar_usuario(params, fake_admin_sdk.tenant_client)

    # Then se envía email_verified=True al SDK y la operación tiene éxito
    assert exit_code == 0
    assert fake_admin_sdk.tenant_client.llamadas_update[0]["cambios"] == {
        "email_verified": True
    }


def test_actualizar_usuario_solo_email_verified_es_permitido(
    fake_admin_sdk: types.SimpleNamespace,
) -> None:
    # Given un uid y únicamente --email-verified (sin email/display_name/password)
    params = ActualizarUsuarioArgs(uid=_UID, email_verified=True)

    # When se ejecuta la actualización
    exit_code = cli_main._actualizar_usuario(params, fake_admin_sdk.tenant_client)

    # Then NO se trata como "sin campos"; se invoca el SDK con éxito (R7.3)
    assert exit_code == 0
    assert len(fake_admin_sdk.tenant_client.llamadas_update) == 1


def test_actualizar_usuario_password_se_envia_y_no_se_imprime(
    fake_admin_sdk: types.SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given un uid y una nueva contraseña (necesaria para signInWithEmailAndPassword)
    contrasena = "S3cretoDePrueba!"
    params = ActualizarUsuarioArgs(uid=_UID, password=contrasena)

    # When se ejecuta la actualización
    exit_code = cli_main._actualizar_usuario(params, fake_admin_sdk.tenant_client)

    # Then la contraseña se envía al SDK en los cambios registrados
    assert exit_code == 0
    assert fake_admin_sdk.tenant_client.llamadas_update[0]["cambios"] == {
        "password": contrasena
    }
    # And la contraseña NUNCA aparece en la salida estándar (sin fuga de secretos)
    salida = capsys.readouterr().out
    assert contrasena not in salida
    assert "actualizado" in salida.lower()


def test_actualizar_usuario_inexistente_aborta_informando_no_encontrado(
    fake_admin_sdk: types.SimpleNamespace,
) -> None:
    # Given un cliente de tenant que lanza UserNotFoundError al actualizar
    def _raise_no_encontrado(uid: str, **cambios: str) -> None:
        raise _FakeUserNotFoundError("no existe")

    fake_admin_sdk.tenant_client.update_user = _raise_no_encontrado
    params = ActualizarUsuarioArgs(uid=_UID, email=_CORREO_VALIDO)

    # When / Then se aborta con OperationError indicando no encontrado (R7.5)
    with pytest.raises(OperationError) as exc_info:
        cli_main._actualizar_usuario(params, fake_admin_sdk.tenant_client)
    assert "no corresponde" in str(exc_info.value).lower()
    assert exc_info.value.exit_code == 4


def test_actualizar_usuario_error_generico_del_sdk_se_traduce_a_operation_error(
    fake_admin_sdk: types.SimpleNamespace,
) -> None:
    # Given un cliente de tenant que lanza un FirebaseError genérico
    def _raise_generico(uid: str, **cambios: str) -> None:
        raise _FakeFirebaseError("fallo interno con detalle sensible")

    fake_admin_sdk.tenant_client.update_user = _raise_generico
    params = ActualizarUsuarioArgs(uid=_UID, email=_CORREO_VALIDO)

    # When / Then se traduce a OperationError sin filtrar el detalle interno
    with pytest.raises(OperationError) as exc_info:
        cli_main._actualizar_usuario(params, fake_admin_sdk.tenant_client)
    assert "detalle sensible" not in str(exc_info.value)


# --- eliminar_usuario -------------------------------------------------------


def test_eliminar_usuario_exitoso_invoca_delete_user_y_retorna_exito(
    fake_admin_sdk: types.SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given un uid existente y un cliente acotado al tenant
    params = EliminarUsuarioArgs(uid=_UID)
    auth_client = fake_admin_sdk.tenant_client

    # When se ejecuta la lógica de baja
    exit_code = cli_main._eliminar_usuario(params, auth_client)

    # Then se invoca delete_user del cliente del tenant con el uid, y se reporta
    # éxito (R7.4)
    assert exit_code == 0
    assert auth_client.llamadas_delete == [{"uid": _UID}]
    assert "eliminado" in capsys.readouterr().out.lower()


def test_eliminar_usuario_inexistente_aborta_informando_no_encontrado(
    fake_admin_sdk: types.SimpleNamespace,
) -> None:
    # Given un cliente de tenant que lanza UserNotFoundError al eliminar
    def _raise_no_encontrado(uid: str) -> None:
        raise _FakeUserNotFoundError("no existe")

    fake_admin_sdk.tenant_client.delete_user = _raise_no_encontrado
    params = EliminarUsuarioArgs(uid=_UID)

    # When / Then se aborta con OperationError indicando no encontrado (R7.5)
    with pytest.raises(OperationError) as exc_info:
        cli_main._eliminar_usuario(params, fake_admin_sdk.tenant_client)
    assert "no corresponde" in str(exc_info.value).lower()
    assert exc_info.value.exit_code == 4


def test_eliminar_usuario_error_generico_del_sdk_se_traduce_a_operation_error(
    fake_admin_sdk: types.SimpleNamespace,
) -> None:
    # Given un cliente de tenant que lanza un FirebaseError genérico
    def _raise_generico(uid: str) -> None:
        raise _FakeFirebaseError("fallo interno con detalle sensible")

    fake_admin_sdk.tenant_client.delete_user = _raise_generico
    params = EliminarUsuarioArgs(uid=_UID)

    # When / Then se traduce a OperationError sin filtrar el detalle interno
    with pytest.raises(OperationError) as exc_info:
        cli_main._eliminar_usuario(params, fake_admin_sdk.tenant_client)
    assert "detalle sensible" not in str(exc_info.value)


# --- flujo completo (main) --------------------------------------------------


def test_main_mapea_exit_code_de_usuario_inexistente_en_actualizacion(
    fake_admin_sdk: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given credenciales inocuas (mocks) y un tenant configurado por entorno
    monkeypatch.setattr(
        cli_main, "resolve_credentials_path", lambda *_a, **_k: "/tmp/fake.json"
    )
    monkeypatch.setattr(
        cli_main, "initialize_firebase_app", lambda *_a, **_k: object()
    )
    monkeypatch.setenv("FIREBASE_TENANT_ID", _TENANT_ID)

    def _raise_no_encontrado(uid: str, **cambios: str) -> None:
        raise _FakeUserNotFoundError("no existe")

    fake_admin_sdk.tenant_client.update_user = _raise_no_encontrado

    # When se ejecuta el flujo completo sobre un uid inexistente
    code = cli_main.main(
        ["actualizar_usuario", "--uid", _UID, "--email", _CORREO_VALIDO],
        command="actualizar_usuario",
    )

    # Then main traduce el OperationError al exit_code específico (4) y la operación
    # se dirigió al cliente acotado al tenant resuelto
    assert code == 4
    assert fake_admin_sdk.tenant_mgt._llamadas[0]["tenant_id"] == _TENANT_ID


def test_main_mapea_exit_code_de_usuario_inexistente_en_baja(
    fake_admin_sdk: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given credenciales inocuas (mocks) y un tenant configurado por entorno
    monkeypatch.setattr(
        cli_main, "resolve_credentials_path", lambda *_a, **_k: "/tmp/fake.json"
    )
    monkeypatch.setattr(
        cli_main, "initialize_firebase_app", lambda *_a, **_k: object()
    )
    monkeypatch.setenv("FIREBASE_TENANT_ID", _TENANT_ID)

    def _raise_no_encontrado(uid: str) -> None:
        raise _FakeUserNotFoundError("no existe")

    fake_admin_sdk.tenant_client.delete_user = _raise_no_encontrado

    # When se ejecuta el flujo completo sobre un uid inexistente
    code = cli_main.main(
        ["eliminar_usuario", "--uid", _UID], command="eliminar_usuario"
    )

    # Then main traduce el OperationError al exit_code específico (4)
    assert code == 4
    assert fake_admin_sdk.tenant_mgt._llamadas[0]["tenant_id"] == _TENANT_ID
