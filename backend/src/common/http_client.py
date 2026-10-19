"""Cliente HTTP saliente hacia proveedores de identidad.

Envuelve ``httpx.AsyncClient`` para aplicar, de forma transversal y consistente,
las garantias transversales exigidas por el diseno en toda llamada saliente que
el Backend_FastAPI origine hacia proveedores (Firebase, F5 APM):

- **Propagacion del Correlation-ID (R10.2):** inyecta en cada solicitud saliente
  el header ``X-Correlation-ID`` con el valor vigente de la solicitud entrante,
  leido desde el contexto compartido (``common.correlation.get_correlation_id``).
  Esto materializa la Propiedad 11: el header saliente es exactamente el mismo
  Correlation-ID que el de la solicitud entrante.
- **Timeout fail-fast de 15 s (R5.1):** aplica un timeout total de 15 segundos a
  cada llamada, de modo que una dependencia lenta falle rapido en vez de bloquear
  el hilo (regla de resiliencia: fail-fast con timeout bajo para transacciones
  cortas).
- **HTTPS obligatorio (R10.5):** antes de transmitir cualquier dato valida que el
  esquema de la URL destino sea ``https`` mediante
  :func:`common.http_security.require_https_scheme` (tarea 5.1). Si el esquema no
  es ``https``, la comunicacion se rechaza sin transmitir datos y el evento se
  registra con el Correlation-ID.

El cliente es importable y testeable: la inyeccion del Correlation-ID se realiza
en :meth:`OutboundHttpClient.request`, lo que permite a la prueba de propiedad
(tarea 5.4) verificar que el header saliente coincide con el Correlation-ID
entrante sin requerir red real (mockeando el transporte de ``httpx``).
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx

from common.api import CORRELATION_ID_HEADER
from common.correlation import get_correlation_id

# Validador de esquema HTTPS de proveedores (tarea 5.1). Se reutiliza tal cual:
# `require_https_scheme` registra el rechazo con el Correlation-ID y eleva
# `InsecureProviderSchemeError` sin transmitir datos (R10.5). Se reexporta el
# error para que quien use el cliente pueda capturarlo sin importar dos modulos.
from common.http_security import (
    InsecureProviderSchemeError,
    require_https_scheme,
)

__all__ = [
    "OUTBOUND_TIMEOUT_SECONDS",
    "InsecureProviderSchemeError",
    "OutboundHttpClient",
]

# Timeout total (en segundos) aplicado a toda llamada saliente a proveedores.
# Valor fail-fast conforme a R5.1 / R3.1 / R4.2 y a la regla de resiliencia.
OUTBOUND_TIMEOUT_SECONDS: float = 15.0


class OutboundHttpClient:
    """Cliente HTTP saliente con Correlation-ID y timeout fail-fast.

    Envuelve un ``httpx.AsyncClient`` y, en cada solicitud, inyecta el
    Correlation-ID vigente (R10.2) y aplica el timeout de 15 s (R5.1), ademas de
    exigir HTTPS hacia el destino (R10.5). Esta pensado para instanciarse por
    solicitud o como dependencia reutilizable; en ambos casos lee el
    Correlation-ID desde el contexto en el momento de cada llamada, por lo que un
    unico cliente puede servir a solicitudes concurrentes sin mezclar
    identificadores.

    Uso como gestor de contexto asincrono::

        async with OutboundHttpClient() as client:
            response = await client.get("https://proveedor.example/recurso")
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        """Inicializa el cliente saliente.

        :param client: ``httpx.AsyncClient`` a envolver. Si es ``None``, se crea
            uno con el timeout fail-fast por defecto. Permitir la inyeccion
            facilita las pruebas (transporte mockeado) sin red real.
        """
        self._client = client if client is not None else httpx.AsyncClient(
            timeout=httpx.Timeout(OUTBOUND_TIMEOUT_SECONDS)
        )
        self._owns_client = client is None

    async def __aenter__(self) -> OutboundHttpClient:
        """Entra en el contexto asincrono devolviendo este mismo cliente."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Cierra el cliente subyacente al salir del contexto si es propio."""
        await self.aclose()

    async def aclose(self) -> None:
        """Cierra el cliente ``httpx`` subyacente si fue creado internamente.

        Si el cliente fue inyectado desde fuera, su ciclo de vida lo gestiona
        quien lo creo y aqui no se cierra.
        """
        if self._owns_client:
            await self._client.aclose()

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Realiza una solicitud saliente con las garantias transversales.

        Pasos aplicados antes de transmitir:
          1. Exige HTTPS hacia el destino mediante
             :func:`common.http_security.require_https_scheme` (R10.5); si el
             esquema no es ``https``, no se transmite dato alguno.
          2. Inyecta el header ``X-Correlation-ID`` con el valor vigente de la
             solicitud entrante (R10.2, Propiedad 11), sin sobrescribir otros
             headers provistos por quien llama.
          3. Aplica el timeout fail-fast de 15 s (R5.1).

        :param method: metodo HTTP (``GET``, ``POST``, ...).
        :param url: URL destino; debe usar esquema ``https``.
        :param headers: headers adicionales de la solicitud. El Correlation-ID se
            agrega sobre una copia, de modo que el diccionario del llamante no se
            muta.
        :param kwargs: parametros adicionales reenviados a ``httpx`` (``json``,
            ``content``, ``params``, etc.).
        :raises InsecureProviderSchemeError: si la URL no usa ``https``.
        :return: la respuesta HTTP del proveedor.
        """
        require_https_scheme(url)

        outgoing_headers = dict(headers or {})
        # Propagacion del Correlation-ID entrante (R10.2). Siempre refleja el
        # valor vigente del contexto, incluso si el llamante no lo proveyo.
        outgoing_headers[CORRELATION_ID_HEADER] = get_correlation_id()

        return await self._client.request(
            method,
            url,
            headers=outgoing_headers,
            timeout=httpx.Timeout(OUTBOUND_TIMEOUT_SECONDS),
            **kwargs,
        )

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """Realiza una solicitud ``GET`` saliente. Ver :meth:`request`."""
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        """Realiza una solicitud ``POST`` saliente. Ver :meth:`request`."""
        return await self.request("POST", url, **kwargs)
