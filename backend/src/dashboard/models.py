"""Modelos (DTO) del dominio del recurso protegido (dashboard).

Define los DTO de salida del dashboard aplicando allowlist de campos y
enmascarado de PII (correo/documento a <=4 caracteres de sufijo). Nunca se
serializan objetos de token completos (regla de seguridad: leakage prevention;
R6.1, R6.4, R6.6, R10.3).
"""

from pydantic import BaseModel, ConfigDict

from common.models import UserType


class MaskedClaim(BaseModel):
    """Claim con posible enmascarado de PII (R6.4, R10.3).

    Los valores sensibles (correo, documento) se enmascaran a lo sumo a los
    ultimos 4 caracteres antes de incluirse en la respuesta.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    # Valor ya enmascarado cuando corresponde (<=4 ultimos caracteres en claro).
    value: str


class DashboardResponse(BaseModel):
    """Datos del recurso protegido (R6.1, R6.4, R6.6).

    `claims` vacio indica ausencia de datos y el frontend muestra el mensaje
    correspondiente sin campos vacios sin indicacion.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str
    user_type: UserType
    claims: list[MaskedClaim]
