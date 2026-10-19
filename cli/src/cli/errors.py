"""Errores de la CLI_Gestion.

Define excepciones específicas (nunca ``Exception`` genérica en lógica de negocio)
para distinguir fallos de credenciales, de validación de parámetros y de operación.
Los mensajes son descriptivos pero nunca exponen el valor de credenciales ni secretos.
"""

from __future__ import annotations


class CliError(Exception):
    """Error base de la CLI_Gestion.

    Attributes:
        exit_code: código de salida sugerido para el proceso.
    """

    exit_code: int = 1


class CredentialsError(CliError):
    """Las credenciales de Firebase no se encontraron o fueron rechazadas (R7.9).

    El mensaje nunca contiene el valor de las credenciales, solo la condición
    (ausencia o invalidez) y, cuando aplica, la ruta o la variable de entorno
    inspeccionada.
    """

    exit_code = 2


class ArgumentValidationError(CliError):
    """Parámetros inválidos, incompletos o con claves no esperadas (R7.7).

    Identifica el parámetro problemático sin alterar datos en Firebase.
    """

    exit_code = 3


class OperationError(CliError):
    """La operación contra Firebase fue abortada por una condición de negocio.

    Cubre casos como un correo duplicado en el alta (R7.2) o un usuario
    inexistente en actualización/baja (R7.5). El mensaje describe la condición
    (p. ej. duplicidad del correo) sin exponer datos sensibles ni la traza del
    Admin SDK, y la operación no deja cambios en Firebase.
    """

    exit_code = 4
