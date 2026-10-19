"""Carga e inicialización de credenciales de Firebase para la CLI_Gestion (R7.8, R7.9).

Las credenciales se obtienen, en orden de precedencia:

1. Una ruta explícita provista por el operador (p. ej. vía ``--credentials``),
   ubicada fuera del control de versiones.
2. La variable de entorno ``GOOGLE_APPLICATION_CREDENTIALS`` apuntando a un
   archivo de cuenta de servicio ubicado fuera del control de versiones.

Este módulo cubre dos responsabilidades:

- ``resolve_credentials_path``: resuelve y valida la *presencia* del archivo de
  credenciales (sin leer su contenido).
- ``initialize_firebase_app``: inicializa el Firebase Admin SDK a partir del
  archivo resuelto, validando que sea una cuenta de servicio utilizable. Si el
  archivo falta, no es legible o es rechazado, aborta con ``CredentialsError``.

Principio de seguridad: ningún mensaje de error expone el contenido del archivo
de credenciales (clave privada, ``client_email``, ``project_id``, etc.); solo se
reporta la condición (ausencia o invalidez estructural) y la ruta/variable
inspeccionada. El Firebase Admin SDK se importa de forma perezosa dentro de la
función de inicialización para que la validación de parámetros no dependa de que
el SDK esté instalado y para no inicializar nada antes de validar la entrada.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from cli.errors import ArgumentValidationError, CredentialsError

if TYPE_CHECKING:  # pragma: no cover - solo para anotaciones de tipo
    from firebase_admin import App
    from firebase_admin.auth import Client as TenantAuthClient

ENV_CREDENTIALS: str = "GOOGLE_APPLICATION_CREDENTIALS"

# Variable de entorno que define el tenant de Identity Platform donde la
# CLI_Gestion administra a los usuarios Cliente de la PoC. Mantener el tenant en
# configuración (no hardcodeado) aísla los usuarios de prueba del resto del
# proyecto y permite cambiar de entorno sin tocar el código.
ENV_TENANT_ID: str = "FIREBASE_TENANT_ID"

# Nombre lógico de la app del Admin SDK para esta CLI; evita colisiones con la
# app por defecto y permite inicializaciones idempotentes.
FIREBASE_APP_NAME: str = "cli-gestion"


def resolve_credentials_path(explicit_path: str | None = None) -> Path:
    """Resuelve la ruta del archivo de credenciales de Firebase.

    Precedencia: ``explicit_path`` (si se provee) sobre la variable de entorno
    ``GOOGLE_APPLICATION_CREDENTIALS``.

    Args:
        explicit_path: ruta opcional provista explícitamente por el operador.

    Returns:
        La ruta canónica (``realpath``) al archivo de credenciales existente.

    Raises:
        CredentialsError: si no se encuentra una ruta configurada, si el archivo
            no existe, o si la ruta no apunta a un archivo regular. El mensaje
            nunca incluye el contenido del archivo.
    """
    raw_path = explicit_path or os.environ.get(ENV_CREDENTIALS)

    if not raw_path or not raw_path.strip():
        raise CredentialsError(
            "No se encontraron credenciales de Firebase: defina la variable de "
            f"entorno {ENV_CREDENTIALS} o provea la ruta del archivo de "
            "credenciales mediante --credentials."
        )

    # Canonicalizar para evitar rutas relativas ambiguas; no se expone el contenido.
    candidate = Path(raw_path.strip()).expanduser().resolve()

    if not candidate.exists():
        raise CredentialsError(
            f"El archivo de credenciales de Firebase no existe en la ruta "
            f"configurada: {candidate}"
        )

    if not candidate.is_file():
        raise CredentialsError(
            f"La ruta de credenciales de Firebase no apunta a un archivo regular: "
            f"{candidate}"
        )

    return candidate


def initialize_firebase_app(credentials_path: Path) -> "App":
    """Inicializa el Firebase Admin SDK a partir del archivo de credenciales.

    Carga el archivo de cuenta de servicio con ``credentials.Certificate`` e
    inicializa (de forma idempotente) una app con nombre ``FIREBASE_APP_NAME``.
    La operación es de solo lectura sobre Firebase: no crea, actualiza ni elimina
    datos; únicamente establece el cliente administrativo que las tareas 13.3/13.4
    usarán para las operaciones de modificación.

    Args:
        credentials_path: ruta canónica a un archivo de cuenta de servicio ya
            resuelto por :func:`resolve_credentials_path`.

    Returns:
        La instancia de ``firebase_admin.App`` inicializada para la CLI.

    Raises:
        CredentialsError: si el Firebase Admin SDK no está disponible, si el
            archivo no es una cuenta de servicio válida/legible, o si el SDK
            rechaza las credenciales. El mensaje nunca incluye el contenido del
            archivo (clave privada, correo de la cuenta de servicio, etc.);
            solo describe la condición y, cuando aplica, la ruta inspeccionada.
    """
    # Importación perezosa: la validación de parámetros ocurre antes y no debe
    # requerir el SDK; además evitamos cualquier efecto colateral del import
    # hasta el momento de inicializar.
    try:
        import firebase_admin
        from firebase_admin import credentials as fb_credentials
    except ImportError as exc:
        raise CredentialsError(
            "El Firebase Admin SDK no está disponible en el entorno; instale la "
            "dependencia 'firebase-admin' desde el repositorio institucional antes "
            "de ejecutar la CLI_Gestion."
        ) from exc

    # Idempotencia: si la app ya fue inicializada en este proceso, reutilizarla.
    try:
        return firebase_admin.get_app(FIREBASE_APP_NAME)
    except ValueError:
        # La app aún no existe; continuar con la inicialización.
        pass

    # Construir el certificado desde el archivo. Un archivo que no sea una cuenta
    # de servicio válida provoca ValueError/IOError; se traduce a CredentialsError
    # sin propagar el contenido ni la traza al operador.
    try:
        certificate = fb_credentials.Certificate(str(credentials_path))
    except (ValueError, IOError) as exc:
        raise CredentialsError(
            "El archivo de credenciales de Firebase no es una cuenta de servicio "
            f"válida o no pudo leerse: {credentials_path}"
        ) from exc

    try:
        return firebase_admin.initialize_app(certificate, name=FIREBASE_APP_NAME)
    except ValueError as exc:
        # p. ej. credenciales rechazadas por formato inesperado al inicializar.
        raise CredentialsError(
            "Firebase rechazó las credenciales proporcionadas; verifique que el "
            f"archivo configurado sea una cuenta de servicio vigente: {credentials_path}"
        ) from exc


def resolve_tenant_id(explicit_tenant: str | None = None) -> str:
    """Resuelve el identificador del tenant de Identity Platform.

    Precedencia: ``explicit_tenant`` (p. ej. provisto vía ``--tenant``) sobre la
    variable de entorno ``FIREBASE_TENANT_ID``. El tenant define el espacio de
    usuarios donde la CLI_Gestion crea, actualiza y elimina usuarios Cliente, de
    modo que los usuarios de prueba de la PoC quedan aislados del resto del
    proyecto. El tenant nunca se codifica en el código fuente.

    Args:
        explicit_tenant: identificador de tenant provisto explícitamente por el
            operador (tiene precedencia sobre la variable de entorno).

    Returns:
        El identificador del tenant, ya recortado de espacios en blanco.

    Raises:
        ArgumentValidationError: si no se configuró ningún tenant (ni por bandera
            ni por variable de entorno). El mensaje describe la condición y la
            variable/bandera esperada, sin exponer secretos.
    """
    raw_tenant = explicit_tenant if explicit_tenant is not None else os.environ.get(ENV_TENANT_ID)

    if not raw_tenant or not raw_tenant.strip():
        raise ArgumentValidationError(
            "No se configuró el tenant de Identity Platform: defina la variable de "
            f"entorno {ENV_TENANT_ID} o provea el identificador mediante --tenant. "
            "El tenant es obligatorio para aislar los usuarios Cliente de la PoC."
        )

    return raw_tenant.strip()


def auth_client_for_tenant(tenant_id: str, app: "App") -> "TenantAuthClient":
    """Obtiene un cliente de autenticación del Admin SDK acotado a un tenant.

    Usa la API documentada del Firebase Admin SDK
    ``firebase_admin.tenant_mgt.auth_for_tenant(tenant_id, app=app)``, que retorna
    un ``firebase_admin.auth.Client`` cuyos métodos ``create_user``/``update_user``/
    ``delete_user`` operan dentro del tenant indicado. Esta función no crea,
    actualiza ni elimina datos; únicamente obtiene el cliente que las operaciones
    de modificación usarán.

    Args:
        tenant_id: identificador del tenant ya resuelto por :func:`resolve_tenant_id`.
        app: instancia de ``firebase_admin.App`` ya inicializada.

    Returns:
        El ``auth.Client`` acotado al tenant.

    Raises:
        CredentialsError: si el Firebase Admin SDK no está disponible en el entorno.
        ArgumentValidationError: si el SDK rechaza el identificador de tenant por
            estar vacío o no ser una cadena válida.
    """
    # Importación perezosa del Admin SDK: coherente con initialize_firebase_app y
    # permite validar la configuración sin requerir el paquete instalado.
    try:
        from firebase_admin import tenant_mgt
    except ImportError as exc:
        raise CredentialsError(
            "El Firebase Admin SDK no está disponible en el entorno; instale la "
            "dependencia 'firebase-admin' desde el repositorio institucional antes "
            "de ejecutar la CLI_Gestion."
        ) from exc

    try:
        return tenant_mgt.auth_for_tenant(tenant_id, app=app)
    except ValueError as exc:
        # El SDK rechaza un tenant vacío o con tipo inesperado. Se traduce a un
        # error de validación de parámetros sin exponer la traza interna.
        raise ArgumentValidationError(
            "El identificador de tenant de Identity Platform no es válido; "
            f"verifique el valor configurado en {ENV_TENANT_ID} o en --tenant."
        ) from exc
