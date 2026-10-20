"""Modelos (DTO) del dominio de autenticacion del Backend_FastAPI.

Define los DTO de entrada/salida del enrutamiento identifier-first y de la
creacion de sesion. Validacion estricta Pydantic v2 (tipo, longitud, formato;
`extra="forbid"`) conforme a Zero Trust Input (R2.3, R5, R10.6).
"""

from pydantic import BaseModel, ConfigDict, Field

from common.models import UserType


class RouteRequest(BaseModel):
    """Entrada del endpoint de enrutamiento (R2.3, R10.6).

    El identificador se normaliza recortando espacios en blanco antes de
    validar su longitud. El servicio clasifica posteriormente si corresponde a
    un correo electronico (Cliente) o a un numero de identificacion (Interno).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # Longitud 5..254; la clasificacion de formato ocurre en la capa de logica.
    identifier: str = Field(min_length=5, max_length=254)


class RouteResponse(BaseModel):
    """Ruta resuelta y metodo de login que el frontend debe iniciar.

    `route_type` puede ser `UserType.CLIENTE`, `UserType.INTERNO` o
    `UserType.CORPORATIVO`. `login_method` es `"password_totp"` para Cliente,
    `"saml_redirect"` para Interno y `"google_redirect"` para Corporativo
    (camino B, Google/Firebase) (R2.1, R2.2). Admitir el valor corporativo no
    cambia la estructura del DTO ni relaja `extra="forbid"`: `route_type` ya es
    un `UserType` ampliado y `login_method` es un `str` libre
    (backward-compatible con los valores existentes).
    """

    model_config = ConfigDict(extra="forbid")

    route_type: UserType
    login_method: str
    correlation_id: str


class SessionRequest(BaseModel):
    """ID token de Firebase a verificar para crear la sesion (R5).

    El token se verifica server-side con el Admin SDK; nunca se procesa un
    cuerpo con claves inesperadas (`extra="forbid"`).
    """

    model_config = ConfigDict(extra="forbid")

    # Longitud 1..4096 acotando el tamano del JWT recibido.
    id_token: str = Field(min_length=1, max_length=4096)
