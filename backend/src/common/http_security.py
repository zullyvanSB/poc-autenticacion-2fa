"""Validacion de transporte seguro hacia proveedores de identidad.

Centraliza la exigencia de HTTPS para toda comunicacion saliente del
Backend_FastAPI hacia proveedores de identidad (R10.5). Expone la funcion pura
:func:`require_https_scheme`, que rechaza cualquier URL cuyo esquema sea
distinto de ``https`` *antes* de que el cliente HTTP saliente transmita dato
alguno, y registra el evento con el Correlation-ID vigente.

El cliente HTTP saliente (tarea 5.3) debe invocar esta validacion como primer
paso de toda llamada; si la URL no supera la verificacion, la comunicacion se
aborta sin establecer conexion ni enviar cuerpo ni cabeceras (fail-fast; regla
de seguridad: SSRF/HTTPS-only, allowlist de esquemas).

El modulo no realiza E/S de red: es puro y determinista, de modo que puede
verificarse mediante property-based testing (tarea 5.2).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from common.logging import get_logger

# Unico esquema de URL permitido para comunicarse con proveedores de identidad.
# Allowlist (no blocklist): cualquier valor distinto se rechaza (R10.5).
_ALLOWED_SCHEME = "https"

# Logger del submodulo; el Correlation-ID y la redaccion de campos sensibles los
# inyecta el formateador estructurado configurado en `common.logging`.
_logger = get_logger("common.http_security")


class InsecureProviderSchemeError(ValueError):
    """La URL de un proveedor usa un esquema distinto de ``https`` (R10.5).

    Se eleva *antes* de transmitir cualquier dato hacia el proveedor. El
    atributo :attr:`scheme` expone unicamente el esquema rechazado (p. ej.
    ``http``, ``ftp`` o cadena vacia) para fines de diagnostico interno; nunca
    conserva la URL completa, que podria contener credenciales o datos
    sensibles en la parte de usuario o de consulta (regla de seguridad: leakage
    prevention).
    """

    def __init__(self, scheme: str) -> None:
        """Construye el error con el esquema rechazado.

        :param scheme: esquema de la URL que motivo el rechazo (sin la URL
            completa, para no filtrar datos sensibles).
        """
        self.scheme: str = scheme
        super().__init__(
            "Esquema de transporte no permitido hacia el proveedor de "
            f"identidad: {scheme!r}. Solo se admite 'https'."
        )


def require_https_scheme(url: str) -> str:
    """Valida que la URL del proveedor use exactamente el esquema ``https``.

    Funcion pura y sin E/S de red: para una misma entrada produce siempre el
    mismo resultado. Extrae el esquema de la URL y lo compara, de forma
    insensible a mayusculas/minusculas, contra la unica allowlist permitida
    (``https``). Si coincide, devuelve la URL sin modificar para que el cliente
    saliente proceda; si no, registra el evento con el Correlation-ID vigente y
    eleva :class:`InsecureProviderSchemeError` sin transmitir dato alguno
    (R10.5).

    El esquema se normaliza a minusculas antes de comparar, de modo que
    ``HTTPS``, ``Https`` y ``https`` se consideran validos (segun RFC 3986, el
    esquema es insensible a mayusculas/minusculas).

    :param url: URL absoluta del proveedor de identidad a validar.
    :return: la misma ``url`` recibida cuando su esquema es ``https``.
    :raises InsecureProviderSchemeError: si el esquema es distinto de
        ``https``. El error no contiene la URL completa.
    """
    scheme = urlsplit(url).scheme.lower()
    if scheme == _ALLOWED_SCHEME:
        return url

    # Se registra el rechazo con el esquema (no la URL completa) para evitar
    # filtrar credenciales o datos de consulta. El Correlation-ID lo inyecta el
    # formateador estructurado (R10.3, R10.5).
    _logger.warning(
        "Comunicacion con proveedor rechazada: esquema de transporte no HTTPS",
        extra={"event": "provider_scheme_rejected", "rejected_scheme": scheme},
    )
    raise InsecureProviderSchemeError(scheme)
