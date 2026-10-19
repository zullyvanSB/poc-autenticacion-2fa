"""Constantes del contrato HTTP del Backend_FastAPI.

Centraliza el prefijo de versionado para que todas las rutas se monten bajo
`/api/v1/` (regla de arquitectura: API versioning con prefijo).
"""

# Prefijo de versionado de la API. Toda ruta HTTP se monta bajo este valor.
API_V1_PREFIX = "/api/v1"

# Nombre del header HTTP que transporta el Correlation-ID entre servicios.
# Se usa tanto para leer el valor entrante como para devolverlo en la respuesta
# y propagarlo en las llamadas salientes (R10.1, R10.2).
CORRELATION_ID_HEADER = "X-Correlation-ID"
