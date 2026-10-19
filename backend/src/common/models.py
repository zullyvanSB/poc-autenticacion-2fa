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

    `CLIENTE` corresponde a la ruta de correo electronico (Firebase/TOTP) e
    `INTERNO` a la ruta de numero de identificacion (SAML hacia F5 APM).
    """

    CLIENTE = "cliente"
    INTERNO = "interno"


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
