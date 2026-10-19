"""Modelos (DTO) transversales del Backend_FastAPI.

Contiene los tipos compartidos entre dominios: el tipo de usuario derivado del
enrutamiento o del proveedor del token (`UserType`) y el DTO de error generico
(`ErrorResponse`). Validacion estricta Pydantic v2: se rechazan claves no
esperadas (`extra="forbid"`) y no se exponen detalles de implementacion al
cliente (regla de seguridad: leakage prevention; R2.4, R10.4, R10.7).
"""

from enum import Enum

from pydantic import BaseModel, ConfigDict


class UserType(str, Enum):
    """Tipo de usuario derivado del enrutamiento o del proveedor del token.

    `CLIENTE` corresponde a la ruta de correo electronico (Firebase/TOTP),
    `INTERNO` a la ruta de numero de identificacion (SAML hacia F5 APM) y
    `CORPORATIVO` a la ruta de correo corporativo que autentica con el
    `GoogleAuthProvider` de Firebase (2FA delegado a Google 2-Step Verification).
    `CORPORATIVO` es distinto de `INTERNO`, de modo que la derivacion por
    proveedor pueda diferenciar SAML (interno) de Google (corporativo).
    """

    CLIENTE = "cliente"
    INTERNO = "interno"
    CORPORATIVO = "corporativo"

    @property
    def label(self) -> str:
        """Etiqueta presentable del tipo de usuario para el dashboard.

        Devuelve un texto legible asociado al miembro, consumible por la capa de
        presentacion sin duplicar literales en cada consumidor. El contrato de
        presentacion vive con el tipo (cohesion).

        :return: la etiqueta presentable del miembro.
        """
        return _USER_TYPE_LABELS[self]


# Etiquetas presentables por tipo de usuario. Centralizadas junto al enum para
# que el dashboard (y demas consumidores) las reutilicen sin duplicar strings.
# Incluye los tres miembros por coherencia de presentacion.
_USER_TYPE_LABELS: dict[UserType, str] = {
    UserType.CLIENTE: "Cliente",
    UserType.INTERNO: "Colaborador interno",
    UserType.CORPORATIVO: "Usuario corporativo",
}


class ErrorResponse(BaseModel):
    """Error generico entregado al cliente, sin detalles de implementacion.

    No revela la regla incumplida, el valor recibido ni trazas de ejecucion
    (R2.4, R10.4, R10.7). El `correlation_id` permite correlacionar con el
    registro interno sin exponer informacion sensible.
    """

    model_config = ConfigDict(extra="forbid")

    # Mensaje generico para el cliente; nunca contiene detalles internos.
    detail: str
    # Correlation-ID (UUID v4) asociado a la solicitud para trazabilidad.
    correlation_id: str
