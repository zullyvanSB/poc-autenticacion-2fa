"""Refuerzo de rate limiting en memoria para los endpoints del backend.

Implementa un limitador de tasa de **ventana fija** con estado efimero en el
proceso (un diccionario con marcas de tiempo) que refuerza los bloqueos
primarios de los proveedores de identidad: el bloqueo de 5 intentos / 300 s de
TOTP de Cliente que aplica Firebase (R3.7) y el bloqueo de 3 intentos del 2FA
corporativo de Interno que aplica F5 APM (R4.10). El bloqueo principal lo
imponen Firebase y F5; el backend solo refuerza el rate limiting de sus
endpoints de enrutamiento (`/api/v1/auth/route`) y de creacion de sesion
(`/api/v1/auth/session`) para contener el abuso directo de esos endpoints
(regla de resiliencia: rate limiting en componentes centrales).

Modelo de ventana fija: para cada clave (ambito del endpoint + clave estable
del cliente, derivada de su IP) se cuenta el numero de solicitudes dentro de una
ventana de duracion fija. Al superar el limite se rechaza con ``429`` hasta que
la ventana expira; una vez transcurrida la ventana (TTL), el contador se
reinicia y las solicitudes vuelven a aceptarse. El cliente nunca recibe el
motivo del rechazo: solo un error generico y una cabecera ``Retry-After``
(R10.4, R10.7).

Limitacion documentada (GCP / Cloud Run): el estado vive en la memoria del
proceso, por lo que **no se comparte entre instancias**. En un despliegue
multi-instancia de Cloud Run cada replica mantiene su propio contador, de modo
que el limite efectivo escala con el numero de instancias. Para un contador
consistente entre instancias se ofrece como opcion gestionada **Memorystore
(Redis)** (ver tarea opcional 15.3); dado que este limitador es un **refuerzo**
y no el bloqueo primario (que aplican Firebase y F5), la aproximacion
per-instancia es aceptable para la PoC.

El limitador es puro respecto a su reloj inyectable (`time_source`), lo que lo
hace deterministicamente verificable por pruebas unitarias sin esperas reales.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitDecision:
    """Resultado de evaluar una solicitud contra el limitador de tasa.

    :ivar allowed: ``True`` si la solicitud esta dentro del limite y procede;
        ``False`` si se supero el limite y debe rechazarse con ``429``.
    :ivar retry_after_seconds: segundos que el cliente debe esperar antes de
        reintentar cuando ``allowed`` es ``False``; ``0`` cuando esta permitida.
        Es el valor para la cabecera ``Retry-After`` (R10.4).
    """

    allowed: bool
    retry_after_seconds: int


class InMemoryRateLimiter:
    """Limitador de tasa de ventana fija con estado efimero en memoria.

    Mantiene, por cada clave, el inicio de la ventana vigente y el numero de
    solicitudes contadas en ella. Al llegar una solicitud:

    1. Si no hay ventana para la clave o la ventana vigente ya expiro (el tiempo
       transcurrido desde su inicio es mayor o igual a ``window_seconds``), se
       abre una nueva ventana con el contador en 1 y se permite (reinicio por
       TTL).
    2. Si la ventana sigue vigente y el contador aun no alcanza
       ``max_requests``, se incrementa el contador y se permite.
    3. Si la ventana sigue vigente y el contador ya alcanzo ``max_requests``, se
       rechaza sin incrementar, devolviendo los segundos restantes de la ventana
       como ``Retry-After``.

    El acceso al estado esta protegido por un cerrojo para ser seguro ante
    solicitudes concurrentes. El reloj es inyectable para pruebas deterministas.
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: int,
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        """Inicializa el limitador con el limite, la ventana y el reloj.

        :param max_requests: numero maximo de solicitudes permitidas por ventana;
            debe ser un entero positivo.
        :param window_seconds: duracion de la ventana fija en segundos; debe ser
            un entero positivo.
        :param time_source: funcion que devuelve un reloj monotono en segundos;
            inyectable para pruebas. Por defecto ``time.monotonic``.
        :raises ValueError: si ``max_requests`` o ``window_seconds`` no son
            enteros positivos.
        """
        if max_requests <= 0:
            raise ValueError("max_requests debe ser un entero positivo.")
        if window_seconds <= 0:
            raise ValueError("window_seconds debe ser un entero positivo.")
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._time_source = time_source
        self._lock = threading.Lock()
        # Estado efimero: clave -> (inicio_de_ventana, contador). Vive solo en la
        # memoria del proceso; no se persiste ni se comparte entre instancias.
        self._windows: dict[str, tuple[float, int]] = {}

    def check(self, key: str) -> RateLimitDecision:
        """Evalua y registra una solicitud para la clave dada.

        Aplica el algoritmo de ventana fija descrito en la clase. Es un metodo
        con efecto: cuando la solicitud se permite, incrementa (o reinicia) el
        contador de la clave. Cuando se rechaza, no modifica el contador.

        :param key: clave estable de la solicitud (ambito del endpoint + clave
            del cliente). Nunca contiene PII sin controlar: se construye con la
            IP del cliente, que no es un claim sensible enmascarable.
        :return: la decision con ``allowed`` y, si procede, ``Retry-After``.
        """
        now = self._time_source()
        with self._lock:
            window = self._windows.get(key)

            # Caso 1: sin ventana previa o la ventana vigente expiro (TTL). Se
            # abre una ventana nueva y se permite la solicitud.
            if window is None or (now - window[0]) >= self._window_seconds:
                self._windows[key] = (now, 1)
                return RateLimitDecision(allowed=True, retry_after_seconds=0)

            window_start, count = window

            # Caso 2: ventana vigente con cupo disponible. Se cuenta y se permite.
            if count < self._max_requests:
                self._windows[key] = (window_start, count + 1)
                return RateLimitDecision(allowed=True, retry_after_seconds=0)

            # Caso 3: ventana vigente sin cupo. Se rechaza con el tiempo restante
            # redondeado hacia arriba a un segundo entero (minimo 1).
            elapsed = now - window_start
            remaining = self._window_seconds - elapsed
            retry_after = max(1, int(remaining) + (1 if remaining % 1 else 0))
            return RateLimitDecision(allowed=False, retry_after_seconds=retry_after)

    def reset(self) -> None:
        """Vacia el estado efimero del limitador.

        Util para pruebas y para reinicios controlados. No afecta a los bloqueos
        primarios de Firebase o F5, que viven en los proveedores.
        """
        with self._lock:
            self._windows.clear()


# Marcador de clave de cliente cuando no es posible determinar la IP de origen.
# Agrupa esas solicitudes bajo una sola clave por ambito en lugar de omitir el
# limite, de modo que el refuerzo nunca quede completamente deshabilitado.
_UNKNOWN_CLIENT_KEY = "unknown"


def client_key_from_host(client_host: str | None) -> str:
    """Deriva la clave estable del cliente a partir de su IP de origen.

    Usa la IP del cliente como clave de agrupacion del rate limiting. La IP no
    es un dato personal sensible enmascarable y se mantiene fuera de los logs de
    aplicacion; solo se usa como clave en memoria. Si la IP no esta disponible
    (p. ej. transporte sin informacion de cliente), se agrupa bajo un marcador
    neutro para no deshabilitar el refuerzo.

    :param client_host: direccion IP del cliente, o ``None`` si se desconoce.
    :return: la clave de cliente a usar en el limitador.
    """
    host = (client_host or "").strip()
    return host or _UNKNOWN_CLIENT_KEY


def build_rate_limit_key(scope: str, client_host: str | None) -> str:
    """Construye la clave del limitador combinando ambito del endpoint y cliente.

    Separa el conteo por endpoint (``scope``) y por cliente (IP), de modo que el
    trafico de un endpoint no agote el cupo de otro y cada cliente tenga su
    propio contador independiente.

    :param scope: identificador del endpoint reforzado (p. ej. ``"auth.route"``).
    :param client_host: IP del cliente, o ``None`` si se desconoce.
    :return: la clave compuesta ``"{scope}:{client}"``.
    """
    return f"{scope}:{client_key_from_host(client_host)}"
