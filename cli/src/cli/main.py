"""Punto de entrada de la CLI_Gestion (esqueleto — tarea 1.4).

Define el parseo de argumentos estricto para los tres comandos de administración
de usuarios Cliente en Firebase:

- ``crear_usuario``
- ``actualizar_usuario``
- ``eliminar_usuario``

El parseo rechaza claves/argumentos no esperados en dos niveles:

1. ``argparse`` configurado con ``allow_abbrev=False`` rechaza flags desconocidos.
2. Los modelos Pydantic con ``extra="forbid"`` rechazan claves no esperadas y
   validan tipo/formato/longitud (R7.7).

Alcance: parseo y validación estricta de parámetros, resolución del tenant de
Identity Platform, resolución de credenciales e inicialización del Firebase Admin
SDK, y la lógica real de cada comando. El orden de ejecución garantiza (R7.7, R7.8,
R7.9 y Property 12) que, ante parámetros inválidos, incompletos o con claves no
esperadas, la operación aborte ANTES de resolver el tenant o las credenciales y,
por tanto, antes de cualquier llamada de modificación al Admin SDK. Las operaciones
se ejecutan sobre un ``auth.Client`` acotado al tenant (resuelto por ``--tenant`` o
``FIREBASE_TENANT_ID``, sin hardcodear), por lo que los usuarios Cliente de la PoC
quedan aislados del resto del proyecto. La lógica de los comandos invoca
``create_user`` (R7.1/R7.2), ``update_user`` (R7.3) y ``delete_user`` (R7.4) del
cliente del tenant, manejando la duplicidad del correo y los usuarios inexistentes
(R7.5).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from pydantic import BaseModel, ValidationError

from cli.credentials import (
    ENV_CREDENTIALS,
    ENV_TENANT_ID,
    auth_client_for_tenant,
    initialize_firebase_app,
    resolve_credentials_path,
    resolve_tenant_id,
)
from cli.errors import ArgumentValidationError, CliError, OperationError
from cli.models import (
    ActualizarUsuarioArgs,
    CrearUsuarioArgs,
    EliminarUsuarioArgs,
)

_PROG = "cli-gestion"


def _format_validation_error(exc: ValidationError) -> str:
    """Convierte un ``ValidationError`` de Pydantic en un mensaje que identifica
    el parámetro inválido o la clave no esperada, sin exponer valores sensibles.

    Args:
        exc: el error de validación lanzado por el modelo de parámetros.

    Returns:
        Mensaje legible que nombra cada parámetro problemático.
    """
    partes: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(item) for item in error.get("loc", ())) or "(desconocido)"
        tipo = error.get("type", "invalido")
        if tipo == "extra_forbidden":
            partes.append(f"parámetro no esperado: '{loc}'")
        elif tipo == "missing":
            partes.append(f"parámetro faltante: '{loc}'")
        else:
            partes.append(f"parámetro inválido: '{loc}' ({tipo})")
    return "; ".join(partes)


def _build_parser() -> argparse.ArgumentParser:
    """Construye el parser con subcomandos estrictos.

    ``allow_abbrev=False`` evita que flags abreviados o desconocidos se acepten
    silenciosamente, reforzando el rechazo de parámetros inesperados.

    Returns:
        El parser raíz con los subcomandos registrados.
    """
    # Los flags --credentials y --tenant se registran tanto en el parser raíz como
    # en cada subparser (mediante un parent) para aceptarlos antes o después del
    # comando.
    credentials_parent = argparse.ArgumentParser(add_help=False)
    credentials_parent.add_argument(
        "--credentials",
        dest="credentials",
        # SUPPRESS evita que el default del subparser sobrescriba el valor dado a
        # nivel raíz; la ausencia se normaliza a None en el dispatch.
        default=argparse.SUPPRESS,
        help=(
            "Ruta al archivo de credenciales de Firebase (cuenta de servicio) "
            f"fuera del control de versiones. Si se omite, se usa {ENV_CREDENTIALS}."
        ),
    )
    credentials_parent.add_argument(
        "--tenant",
        dest="tenant",
        # SUPPRESS: la ausencia a nivel raíz no sobrescribe el valor del subparser;
        # el dispatch normaliza la ausencia a None y resuelve con FIREBASE_TENANT_ID.
        default=argparse.SUPPRESS,
        help=(
            "Identificador del tenant de Identity Platform donde se administran "
            f"los usuarios Cliente. Si se omite, se usa {ENV_TENANT_ID}. Tiene "
            "precedencia sobre la variable de entorno."
        ),
    )

    parser = argparse.ArgumentParser(
        prog=_PROG,
        description=(
            "CLI de administración de usuarios Cliente en Firebase (PoC 2FA)."
        ),
        allow_abbrev=False,
        parents=[credentials_parent],
    )

    sub = parser.add_subparsers(dest="command", required=True, metavar="comando")

    crear = sub.add_parser(
        "crear_usuario",
        help="Crea un usuario Cliente en Firebase.",
        allow_abbrev=False,
        parents=[credentials_parent],
    )
    crear.add_argument("--email", required=True, help="Correo del usuario Cliente.")

    actualizar = sub.add_parser(
        "actualizar_usuario",
        help="Actualiza un usuario Cliente existente en Firebase.",
        allow_abbrev=False,
        parents=[credentials_parent],
    )
    actualizar.add_argument("--uid", required=True, help="UID del usuario Cliente.")
    actualizar.add_argument("--email", default=None, help="Nuevo correo (opcional).")
    actualizar.add_argument(
        "--display-name",
        dest="display_name",
        default=None,
        help="Nuevo nombre para mostrar (opcional).",
    )
    actualizar.add_argument(
        "--email-verified",
        dest="email_verified",
        action=argparse.BooleanOptionalAction,
        # SUPPRESS: si no se indica ni --email-verified ni --no-email-verified, la
        # clave queda ausente del namespace y el dispatch la omite (coherente con
        # la extracción que descarta valores None/no provistos).
        default=argparse.SUPPRESS,
        help=(
            "Marca o desmarca el correo como verificado "
            "(--email-verified / --no-email-verified). Firebase exige el correo "
            "verificado para enrolar TOTP/MFA (opcional)."
        ),
    )
    actualizar.add_argument(
        "--password",
        default=None,
        help=(
            "Nueva contraseña del usuario Cliente (mínimo 6 caracteres). Nunca se "
            "registra ni se imprime (opcional)."
        ),
    )

    eliminar = sub.add_parser(
        "eliminar_usuario",
        help="Elimina un usuario Cliente de Firebase.",
        allow_abbrev=False,
        parents=[credentials_parent],
    )
    eliminar.add_argument("--uid", required=True, help="UID del usuario Cliente.")

    return parser


def _validate_args(command: str, raw: dict[str, object]) -> BaseModel:
    """Valida los parámetros del comando con el modelo Pydantic correspondiente.

    Args:
        command: nombre del subcomando.
        raw: diccionario de parámetros ya parseados por argparse (solo las claves
            propias del comando, sin ``None`` opcionales no provistos).

    Returns:
        La instancia validada del modelo de parámetros del comando.

    Raises:
        ArgumentValidationError: si los parámetros son inválidos, incompletos o
            incluyen claves no esperadas (R7.7).
    """
    modelos = {
        "crear_usuario": CrearUsuarioArgs,
        "actualizar_usuario": ActualizarUsuarioArgs,
        "eliminar_usuario": EliminarUsuarioArgs,
    }
    modelo = modelos.get(command)
    if modelo is None:  # pragma: no cover - argparse ya restringe los comandos
        raise ArgumentValidationError(f"Comando no soportado: '{command}'.")

    try:
        return modelo.model_validate(raw)
    except ValidationError as exc:
        raise ArgumentValidationError(_format_validation_error(exc)) from exc


def _crear_usuario(params: CrearUsuarioArgs, auth_client: object) -> int:
    """Crea un usuario Cliente dentro del tenant de Identity Platform (R7.1, R7.2).

    En este punto el correo ya fue validado de forma estricta (tipo, longitud 5–254
    y formato RFC 5322), las credenciales fueron aceptadas y el tenant fue resuelto.
    Se invoca ``create_user(email=...)`` sobre el ``auth.Client`` acotado al tenant,
    de modo que el usuario se crea dentro del tenant de la PoC y no a nivel del
    proyecto. Si el correo ya corresponde a un usuario existente en el tenant, el
    SDK lanza ``EmailAlreadyExistsError``: se aborta sin crear ni modificar nada y
    se informa la duplicidad (R7.2) sin exponer la traza del SDK. En caso de éxito
    se imprime el ``uid`` del usuario creado junto con un indicador de éxito (R7.1).

    Args:
        params: parámetros validados del comando de alta.
        auth_client: ``firebase_admin.auth.Client`` acotado al tenant.

    Returns:
        ``0`` cuando el usuario se crea correctamente.

    Raises:
        OperationError: si el correo ya existe en Firebase (duplicidad, R7.2) o si
            el Admin SDK rechaza la creación por otra condición de autenticación.
            El mensaje nunca incluye la traza interna del SDK.
    """
    # Importación perezosa del Admin SDK: solo para resolver las clases de error;
    # las operaciones se ejecutan sobre el cliente acotado al tenant. Coherente con
    # credentials.py y permite parchear sin requerir el paquete instalado.
    from firebase_admin import auth, exceptions

    try:
        user_record = auth_client.create_user(email=params.email)
    except auth.EmailAlreadyExistsError as exc:
        # R7.2: duplicidad del correo. No se creó ni modificó usuario alguno.
        # No se expone el valor del correo ni la traza del SDK en la salida.
        raise OperationError(
            "No se creó el usuario: el correo ya corresponde a un usuario Cliente "
            "existente en Firebase."
        ) from exc
    except exceptions.FirebaseError as exc:
        # Otras condiciones del Admin SDK (FirebaseError es la base de todas) se
        # traducen a un error genérico de operación sin filtrar detalles internos.
        raise OperationError(
            "No se pudo crear el usuario Cliente en Firebase debido a un error de "
            "la operación de autenticación."
        ) from exc

    # R7.1: éxito. Se reporta el identificador único del usuario creado.
    print(f"Usuario creado correctamente. uid={user_record.uid}")
    return 0


def _actualizar_usuario(params: ActualizarUsuarioArgs, auth_client: object) -> int:
    """Actualiza un usuario Cliente del tenant por su ``uid`` (R7.3, R7.5).

    Los parámetros ya fueron validados de forma estricta (``uid`` presente, y
    ``email``/``display_name`` opcionales con formato/longitud válidos), las
    credenciales fueron aceptadas y el tenant fue resuelto. Solo se envían al Admin
    SDK los campos que el operador proporcionó explícitamente; si no se indica
    ningún campo modificable, la actualización se aborta como parámetros incompletos
    (R7.7) sin tocar el SDK. Se invoca ``update_user(uid, **cambios)`` sobre el
    ``auth.Client`` acotado al tenant. Si el ``uid`` no corresponde a un usuario
    existente en el tenant, el SDK lanza ``UserNotFoundError``: se aborta sin
    alterar dato alguno y se informa que el usuario no fue encontrado (R7.5) sin
    exponer la traza del SDK. En caso de éxito se imprime un indicador de éxito
    (R7.3).

    Args:
        params: parámetros validados del comando de actualización.
        auth_client: ``firebase_admin.auth.Client`` acotado al tenant.

    Returns:
        ``0`` cuando el usuario se actualiza correctamente.

    Raises:
        ArgumentValidationError: si no se proporcionó ningún campo modificable
            (``email``, ``display_name``, ``email_verified`` ni ``password``); se
            aborta antes de invocar el SDK.
        OperationError: si el ``uid`` no existe en Firebase (R7.5) o si el Admin
            SDK rechaza la actualización por otra condición de autenticación. El
            mensaje nunca incluye la traza interna del SDK.
    """
    # Solo se envían los campos explícitamente provistos por el operador. Esto
    # evita sobrescribir con None atributos no indicados en la invocación. El SDK
    # (firebase-admin 6.6.0) acepta email_verified=<bool> y password=<str> en
    # update_user.
    cambios: dict[str, object] = {}
    if params.email is not None:
        cambios["email"] = params.email
    if params.display_name is not None:
        cambios["display_name"] = params.display_name
    if params.email_verified is not None:
        cambios["email_verified"] = params.email_verified
    if params.password is not None:
        cambios["password"] = params.password

    if not cambios:
        # Parámetros incompletos: no hay nada que actualizar. Se aborta sin tocar
        # el SDK (R7.7), identificando los parámetros modificables esperados. No se
        # incluye el contenido de 'cambios' en el mensaje (nunca se filtra la
        # contraseña ni otros datos del usuario).
        raise ArgumentValidationError(
            "No se indicó ningún dato para actualizar; proporcione al menos uno de "
            "los parámetros: '--email', '--display-name', '--email-verified' o "
            "'--password'."
        )

    # Importación perezosa del Admin SDK: solo para resolver las clases de error;
    # la operación se ejecuta sobre el cliente acotado al tenant.
    from firebase_admin import auth, exceptions

    try:
        auth_client.update_user(params.uid, **cambios)
    except auth.UserNotFoundError as exc:
        # R7.5: el uid no corresponde a un usuario existente. No se alteró dato
        # alguno. No se expone el uid ni la traza del SDK en la salida.
        raise OperationError(
            "No se actualizó el usuario: el identificador proporcionado no "
            "corresponde a ningún usuario Cliente en Firebase."
        ) from exc
    except exceptions.FirebaseError as exc:
        # Otras condiciones del Admin SDK se traducen a un error genérico de
        # operación sin filtrar detalles internos.
        raise OperationError(
            "No se pudo actualizar el usuario Cliente en Firebase debido a un error "
            "de la operación de autenticación."
        ) from exc

    # R7.3: éxito. No se expone el uid completo ni datos sensibles del usuario.
    print("Usuario actualizado correctamente.")
    return 0


def _eliminar_usuario(params: EliminarUsuarioArgs, auth_client: object) -> int:
    """Elimina un usuario Cliente del tenant por su ``uid`` (R7.4, R7.5).

    El ``uid`` ya fue validado de forma estricta, las credenciales fueron aceptadas
    y el tenant fue resuelto. Se invoca ``delete_user(uid)`` sobre el ``auth.Client``
    acotado al tenant. Si el ``uid`` no corresponde a un usuario existente en el
    tenant, el SDK lanza ``UserNotFoundError``: se aborta sin alterar dato alguno y
    se informa que el usuario no fue encontrado (R7.5) sin exponer la traza del SDK.
    En caso de éxito se imprime un indicador de éxito (R7.4).

    Args:
        params: parámetros validados del comando de baja.
        auth_client: ``firebase_admin.auth.Client`` acotado al tenant.

    Returns:
        ``0`` cuando el usuario se elimina correctamente.

    Raises:
        OperationError: si el ``uid`` no existe en Firebase (R7.5) o si el Admin
            SDK rechaza la baja por otra condición de autenticación. El mensaje
            nunca incluye la traza interna del SDK.
    """
    # Importación perezosa del Admin SDK: solo para resolver las clases de error;
    # la operación se ejecuta sobre el cliente acotado al tenant.
    from firebase_admin import auth, exceptions

    try:
        auth_client.delete_user(params.uid)
    except auth.UserNotFoundError as exc:
        # R7.5: el uid no corresponde a un usuario existente. No se alteró dato
        # alguno. No se expone el uid ni la traza del SDK en la salida.
        raise OperationError(
            "No se eliminó el usuario: el identificador proporcionado no "
            "corresponde a ningún usuario Cliente en Firebase."
        ) from exc
    except exceptions.FirebaseError as exc:
        # Otras condiciones del Admin SDK se traducen a un error genérico de
        # operación sin filtrar detalles internos.
        raise OperationError(
            "No se pudo eliminar el usuario Cliente en Firebase debido a un error "
            "de la operación de autenticación."
        ) from exc

    # R7.4: éxito. No se expone el uid completo ni datos sensibles del usuario.
    print("Usuario eliminado correctamente.")
    return 0


def _run_command(command: str, params: BaseModel, auth_client: object) -> int:
    """Ejecuta el comando ya validado contra el cliente acotado al tenant.

    En este punto los parámetros ya fueron validados de forma estricta, el Firebase
    Admin SDK ya está inicializado con credenciales válidas y el ``auth.Client`` ya
    está acotado al tenant de Identity Platform resuelto. El comando
    ``crear_usuario`` invoca ``create_user`` y maneja la duplicidad del correo
    (R7.1, R7.2); ``actualizar_usuario`` invoca ``update_user`` y
    ``eliminar_usuario`` invoca ``delete_user``, ambos manejando el caso de usuario
    inexistente (R7.3, R7.4, R7.5). Todas las operaciones ocurren dentro del tenant.

    Args:
        command: nombre del subcomando validado.
        params: instancia de parámetros validada (DTO permitido).
        auth_client: ``firebase_admin.auth.Client`` acotado al tenant.

    Returns:
        Código de salida del proceso.

    Raises:
        NotImplementedError: si el subcomando no tiene un manejador asociado
            (invariante de dispatch; argparse ya restringe los comandos válidos).
    """
    if command == "crear_usuario":
        assert isinstance(params, CrearUsuarioArgs)  # noqa: S101 - invariante de dispatch
        return _crear_usuario(params, auth_client)

    if command == "actualizar_usuario":
        assert isinstance(params, ActualizarUsuarioArgs)  # noqa: S101 - invariante de dispatch
        return _actualizar_usuario(params, auth_client)

    if command == "eliminar_usuario":
        assert isinstance(params, EliminarUsuarioArgs)  # noqa: S101 - invariante de dispatch
        return _eliminar_usuario(params, auth_client)

    # Invariante de dispatch: argparse ya restringe los comandos válidos, de modo
    # que este punto no debería alcanzarse en operación normal.
    raise NotImplementedError(  # pragma: no cover - inalcanzable por el parser
        f"El comando '{command}' no tiene un manejador asociado."
    )


def _dispatch(argv: Sequence[str] | None, command: str | None) -> int:
    """Orquesta el parseo, la validación estricta y la resolución de credenciales.

    Args:
        argv: argumentos de línea de comandos (sin el nombre del programa).
        command: subcomando forzado cuando se invoca por un entrypoint dedicado,
            o ``None`` para inferirlo de ``argv``.

    Returns:
        Código de salida del proceso.
    """
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    resolved_command = command or args.command
    namespace = vars(args)

    # 'credentials' y 'tenant' pueden estar ausentes (SUPPRESS) si no se dieron.
    credentials_arg = namespace.get("credentials")
    tenant_arg = namespace.get("tenant")

    # Extraer solo las claves del comando; argparse incluye 'command', 'credentials'
    # y 'tenant', que no son parámetros del modelo de cada comando.
    raw = {
        key: value
        for key, value in namespace.items()
        if key not in {"command", "credentials", "tenant"} and value is not None
    }

    # 1) Validación estricta de parámetros ANTES de tocar credenciales u operar.
    #    Si falla, se aborta aquí (R7.7, Property 12) sin resolver credenciales
    #    ni invocar ninguna API de modificación.
    params = _validate_args(resolved_command, raw)

    # 2) Resolver el tenant de Identity Platform ANTES de cualquier operación de
    #    modificación. Si no está configurado (ni --tenant ni FIREBASE_TENANT_ID),
    #    se aborta aquí sin tocar el Admin SDK, garantizando que los usuarios
    #    Cliente se administren siempre dentro del tenant de la PoC.
    tenant_id = resolve_tenant_id(tenant_arg)

    # 3) Resolver e inicializar credenciales ANTES de cualquier operación de
    #    modificación (R7.8, R7.9). Si faltan o son inválidas, se aborta aquí sin
    #    alterar datos y sin exponer el valor de las credenciales.
    credentials_path: Path = resolve_credentials_path(credentials_arg)
    app = initialize_firebase_app(credentials_path)

    # 4) Obtener el cliente de autenticación acotado al tenant resuelto.
    auth_client = auth_client_for_tenant(tenant_id, app)

    # 5) Solo con parámetros válidos, credenciales aceptadas y el cliente acotado
    #    al tenant se ejecuta el comando.
    return _run_command(resolved_command, params, auth_client)


def main(argv: Sequence[str] | None = None, command: str | None = None) -> int:
    """Punto de entrada común: traduce errores de la CLI a códigos de salida.

    Args:
        argv: argumentos de línea de comandos (sin el nombre del programa).
        command: subcomando forzado para entrypoints dedicados.

    Returns:
        Código de salida del proceso.
    """
    try:
        return _dispatch(argv, command)
    except CliError as exc:
        # Mensaje genérico/identificador de parámetro, sin exponer secretos.
        print(f"Error: {exc}", file=sys.stderr)
        return exc.exit_code
    except NotImplementedError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def crear_usuario_entrypoint() -> NoReturn:
    """Entrypoint de consola para ``crear_usuario``."""
    sys.exit(main(["crear_usuario", *sys.argv[1:]], command="crear_usuario"))


def actualizar_usuario_entrypoint() -> NoReturn:
    """Entrypoint de consola para ``actualizar_usuario``."""
    sys.exit(main(["actualizar_usuario", *sys.argv[1:]], command="actualizar_usuario"))


def eliminar_usuario_entrypoint() -> NoReturn:
    """Entrypoint de consola para ``eliminar_usuario``."""
    sys.exit(main(["eliminar_usuario", *sys.argv[1:]], command="eliminar_usuario"))


if __name__ == "__main__":
    sys.exit(main())
