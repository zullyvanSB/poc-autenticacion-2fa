"""Pruebas unitarias de la carga e inicialización de credenciales (R7.8, R7.9).

Verifican que:

- La resolución de la ruta de credenciales respeta la precedencia (ruta explícita
  sobre variable de entorno) y aborta con ``CredentialsError`` cuando falta o es
  inválida, sin exponer el valor del archivo.
- La inicialización del Firebase Admin SDK traduce la ausencia del SDK y los
  archivos inválidos a ``CredentialsError`` sin filtrar el contenido del archivo.

Las pruebas no requieren el paquete ``firebase-admin``: el import es perezoso y se
ejercita precisamente la rama que aborta cuando el SDK no está disponible.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli.credentials import (
    ENV_CREDENTIALS,
    initialize_firebase_app,
    resolve_credentials_path,
)
from cli.errors import CredentialsError

# Valor sentinela que simula una clave privada; nunca debe aparecer en los errores.
_SECRET_SENTINEL = "PRIVATE-KEY-SENTINEL-DO-NOT-LEAK"


def test_resuelve_ruta_explicita_sobre_variable_entorno(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given un archivo explícito y una variable de entorno apuntando a otro
    explicit = tmp_path / "explicit.json"
    explicit.write_text("{}", encoding="utf-8")
    env_file = tmp_path / "env.json"
    env_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(ENV_CREDENTIALS, str(env_file))

    # When se resuelve con ruta explícita
    resolved = resolve_credentials_path(str(explicit))

    # Then prevalece la ruta explícita (canonicalizada)
    assert resolved == explicit.resolve()


def test_resuelve_desde_variable_entorno_cuando_no_hay_ruta_explicita(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given solo la variable de entorno configurada
    env_file = tmp_path / "env.json"
    env_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(ENV_CREDENTIALS, str(env_file))

    # When se resuelve sin ruta explícita
    resolved = resolve_credentials_path(None)

    # Then se usa la ruta de la variable de entorno
    assert resolved == env_file.resolve()


def test_aborta_si_no_hay_credenciales_configuradas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given sin variable de entorno ni ruta explícita
    monkeypatch.delenv(ENV_CREDENTIALS, raising=False)

    # When / Then aborta con CredentialsError mencionando la variable esperada
    with pytest.raises(CredentialsError) as exc_info:
        resolve_credentials_path(None)
    assert ENV_CREDENTIALS in str(exc_info.value)


def test_aborta_si_el_archivo_no_existe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given una ruta inexistente
    monkeypatch.delenv(ENV_CREDENTIALS, raising=False)
    missing = tmp_path / "no-existe.json"

    # When / Then aborta con CredentialsError
    with pytest.raises(CredentialsError):
        resolve_credentials_path(str(missing))


def test_aborta_si_la_ruta_es_un_directorio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given una ruta que apunta a un directorio
    monkeypatch.delenv(ENV_CREDENTIALS, raising=False)

    # When / Then aborta porque no es un archivo regular
    with pytest.raises(CredentialsError):
        resolve_credentials_path(str(tmp_path))


def test_init_aborta_sin_firebase_admin_disponible(tmp_path: Path) -> None:
    # Given un archivo de credenciales presente (contenido no se inspecciona aquí)
    cred_file = tmp_path / "service-account.json"
    cred_file.write_text(
        json.dumps({"type": "service_account", "private_key": _SECRET_SENTINEL}),
        encoding="utf-8",
    )

    # When el SDK no está instalado en el entorno de pruebas
    pytest.importorskip  # noqa: B018 - documenta la dependencia opcional
    try:
        import firebase_admin  # noqa: F401
    except ImportError:
        firebase_admin_disponible = False
    else:
        firebase_admin_disponible = True

    if firebase_admin_disponible:
        pytest.skip("firebase-admin está instalado; esta rama cubre su ausencia")

    # Then aborta con CredentialsError sin exponer el contenido del archivo
    with pytest.raises(CredentialsError) as exc_info:
        initialize_firebase_app(cred_file.resolve())
    mensaje = str(exc_info.value)
    assert "firebase-admin" in mensaje
    assert _SECRET_SENTINEL not in mensaje


def test_init_no_filtra_contenido_del_archivo_en_errores(tmp_path: Path) -> None:
    # Given un archivo que contiene un secreto sentinela
    cred_file = tmp_path / "service-account.json"
    cred_file.write_text(
        json.dumps({"type": "service_account", "private_key": _SECRET_SENTINEL}),
        encoding="utf-8",
    )

    # When falla la inicialización (por cualquier causa en este entorno)
    try:
        initialize_firebase_app(cred_file.resolve())
    except CredentialsError as exc:
        # Then el mensaje nunca contiene el secreto del archivo
        assert _SECRET_SENTINEL not in str(exc)
    # Si no lanza (SDK instalado y archivo válido), la invariante de no-filtración
    # igualmente se cumple porque no hubo mensaje de error.
