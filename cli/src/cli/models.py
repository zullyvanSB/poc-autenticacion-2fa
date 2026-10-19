"""Modelos de parámetros de la CLI_Gestion (parseo estricto con Pydantic v2).

Cada comando tiene un modelo con ``ConfigDict(extra="forbid")`` para rechazar
claves no esperadas (R7.7) y validación de tipo/formato/longitud server-side
(Zero Trust Input). Estos modelos definen el *contrato* de parámetros; la
ejecución real de las operaciones contra el Admin SDK se implementa en la
tarea 13.

Nota de alcance (tarea 1.4): aquí se define el esqueleto de validación. Las
reglas de formato de correo (RFC 5322, 5–254) y de ``uid`` se afinarán junto
con la lógica de los comandos en 13.3/13.4; de momento se aplican restricciones
básicas de presencia y longitud.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Longitudes alineadas con el diseño: correo 5–254, uid de Firebase hasta 128.
EMAIL_MIN_LENGTH: int = 5
EMAIL_MAX_LENGTH: int = 254
UID_MIN_LENGTH: int = 1
UID_MAX_LENGTH: int = 128

# Patrón de formato de correo alineado con RFC 5322 (forma práctica/no backtracking):
# una parte local addr-spec (átomos dot-atom, sin puntos consecutivos ni al borde),
# una @, y un dominio de etiquetas alfanuméricas separadas por puntos con un TLD
# alfabético de al menos dos caracteres. Es un allowlist de formato (Zero Trust
# Input): rechaza por defecto cualquier cadena que no se ajuste a la estructura.
_EMAIL_PATTERN = re.compile(
    r"^(?=.{1,64}@)"  # parte local acotada a 64 caracteres (RFC 5321)
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@"
    r"(?=.{1,255}$)"  # dominio acotado a 255 caracteres (RFC 1035)
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}$"
)


def _validate_email_format(value: str) -> str:
    """Valida que ``value`` tenga formato de correo válido (RFC 5322, allowlist).

    La longitud (5–254) ya la restringe el ``Field``; aquí se verifica únicamente
    el *formato*. Se aplica un allowlist estricto: toda cadena que no se ajuste al
    patrón se rechaza (Zero Trust Input).

    Args:
        value: el correo normalizado (ya recortado por ``str_strip_whitespace``).

    Returns:
        El mismo correo cuando su formato es válido.

    Raises:
        ValueError: si el correo no cumple el formato esperado. Pydantic lo
            traduce a un ``ValidationError`` que la capa de CLI convierte en un
            mensaje que identifica el parámetro inválido sin exponer su valor.
    """
    if not _EMAIL_PATTERN.match(value):
        raise ValueError("formato de correo inválido (se espera un correo RFC 5322)")
    return value


class CrearUsuarioArgs(BaseModel):
    """Parámetros del comando ``crear_usuario`` (R7.1).

    Attributes:
        email: correo electrónico del usuario Cliente a crear.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: str = Field(min_length=EMAIL_MIN_LENGTH, max_length=EMAIL_MAX_LENGTH)

    @field_validator("email")
    @classmethod
    def _email_formato_valido(cls, value: str) -> str:
        """Valida el formato RFC 5322 del correo tras la verificación de longitud."""
        return _validate_email_format(value)


class ActualizarUsuarioArgs(BaseModel):
    """Parámetros del comando ``actualizar_usuario`` (R7.3).

    Attributes:
        uid: identificador único del usuario Cliente existente.
        email: nuevo correo electrónico (opcional).
        display_name: nuevo nombre para mostrar (opcional).
        email_verified: nuevo estado de verificación del correo (opcional).
            Firebase exige ``email_verified=True`` para habilitar el enrolamiento
            de MFA/TOTP del usuario Cliente.
        password: nueva contraseña (opcional). Firebase requiere una longitud
            mínima de 6 caracteres; es necesaria para el inicio de sesión con
            correo y contraseña del flujo TOTP. Nunca se registra ni se imprime.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    uid: str = Field(min_length=UID_MIN_LENGTH, max_length=UID_MAX_LENGTH)
    email: str | None = Field(
        default=None, min_length=EMAIL_MIN_LENGTH, max_length=EMAIL_MAX_LENGTH
    )
    display_name: str | None = Field(default=None, min_length=1, max_length=256)
    email_verified: bool | None = Field(default=None)
    password: str | None = Field(default=None, min_length=6, max_length=4096)


class EliminarUsuarioArgs(BaseModel):
    """Parámetros del comando ``eliminar_usuario`` (R7.4).

    Attributes:
        uid: identificador único del usuario Cliente a eliminar.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    uid: str = Field(min_length=UID_MIN_LENGTH, max_length=UID_MAX_LENGTH)
