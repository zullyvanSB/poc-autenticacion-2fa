# Backend_FastAPI — PoC Autenticación 2FA

Servicio en Python 3.12+/FastAPI que decide el enrutamiento Cliente/Interno,
verifica tokens de Firebase, entrega la sesión por cookie segura y protege el
dashboard. La especificación OpenAPI es la fuente de verdad del contrato.

## Estructura por dominio

```
backend/
  pyproject.toml        # Poetry, dependencias fijadas a versión exacta (JFrog)
  src/
    main.py             # App FastAPI base, router /api/v1/, OpenAPI
    auth/               # Dominio de autenticación (ruta, sesión, logout)
    dashboard/          # Dominio del recurso protegido
    common/             # Capa transversal (contrato, logging, errores)
```

## Contrato de API

- Todas las rutas se montan bajo el prefijo `/api/v1/`.
- OpenAPI: `GET /api/v1/openapi.json`
- Docs: `GET /api/v1/docs`

## Requisitos

- Python 3.12 o superior
- Poetry (dependencias resueltas desde el repositorio institucional JFrog)
- Toda la configuración y los secretos se obtienen de variables de entorno; no
  hay valores embebidos en el código ni en la imagen.

## Configuración por entorno (sin secretos en código ni imagen)

El backend obtiene **toda** su configuración y secretos de variables de entorno
(R9.3). No hay valores embebidos en el código ni en la imagen. Al arrancar, el
`StartupValidator` aborta si falta alguna variable requerida, indicando el
nombre de cada una sin exponer valores (R9.4).

Para desarrollo local, define las variables en un archivo `.env` **fuera del
control de versiones** (ya está en `.gitignore` y `.dockerignore`). Nunca
subas `.env` ni archivos de credenciales (`*serviceAccount*.json`,
`*.key`, `*.pem`) al repositorio.

Ejemplo de `.env` local (valores de referencia, reemplázalos por los reales):

```dotenv
# Puerto de escucha (Cloud Run inyecta PORT; en local el default de la imagen es 8080)
PORT=8080
# Credenciales del Firebase Admin SDK: ruta a un archivo fuera de VCS
GOOGLE_APPLICATION_CREDENTIALS=/secrets/serviceAccount.json
# ...resto de variables requeridas por StartupValidator
```

### Variables opcionales por entorno: `APP_ENV` y `CORS_ALLOWED_ORIGINS`

Estas variables son **opcionales** y tienen valores por defecto seguros
(fail-safe hacia producción); no forman parte de las variables requeridas por
`StartupValidator`.

- **`APP_ENV`** (`local` | `production`): selecciona el perfil de entorno. Si
  está ausente, vacía o tiene cualquier valor distinto de `local`, se interpreta
  como `production` (fail-safe a seguro). El perfil ajusta los atributos de la
  **session cookie**:
  - `production` (por defecto): cookie `Secure` + `SameSite=Strict`.
  - `local`: cookie **no-`Secure`** + `SameSite=Lax`, para permitir el flujo de
    la cookie sobre HTTP plano del dev server. Este relajamiento aplica **solo**
    en desarrollo local; producción nunca se debilita.
- **`CORS_ALLOWED_ORIGINS`**: lista de orígenes permitidos separados por comas.
  Si está vacía:
  - en `local` usa por defecto `http://localhost:4200,http://127.0.0.1:4200`
    (dev server de Angular);
  - en `production` queda vacía y **no se registra** middleware CORS.
  Si defines orígenes explícitos, se respetan tal cual en cualquier entorno.

Para desarrollo local cross-origin (Angular en `http://localhost:4200`, backend
en `http://127.0.0.1:8081`), activa el perfil local en tu `.env`:

```dotenv
APP_ENV=local
# Opcional: sobreescribe los orígenes CORS por defecto
# CORS_ALLOWED_ORIGINS=http://localhost:4200,http://127.0.0.1:4200
```

> **NOTA (tradeoff de `SameSite=Lax` en local).** A efectos de `SameSite`, los
> distintos **puertos** del mismo dominio registrable (`localhost`) se consideran
> same-site, pero **hosts distintos** no: `http://localhost:4200` y
> `http://127.0.0.1:8081` son cross-site por diferir en el host. Para que la
> cookie de sesión fluya en local, el frontend debe alcanzar al backend usando el
> **mismo hostname** (p. ej. si la app corre en `http://localhost:4200`, llama al
> backend en `http://localhost:8081`), o bien configurar el **proxy de Angular**
> para enrutar las llamadas al backend a través del mismo origen.

## Ejecución local

### Opción A — Imagen Docker única (misma imagen que Cloud Run, R9.2)

La imagen es **idéntica en digest** y sirve tanto en local como en Cloud Run
sin reconstrucción. Los secretos y la configuración se inyectan en tiempo de
ejecución con `--env-file`, apuntando a un `.env` fuera del control de
versiones; **no se hornea ningún secreto en la imagen**.

```bash
# 1) Construir la imagen (desde la raíz del repo).
#    Las credenciales de JFrog se pasan como build args efímeros y NO quedan
#    en la imagen final (se usan solo en la etapa builder).
docker build \
  --build-arg JFROG_USER="$JFROG_USER" \
  --build-arg JFROG_PASSWORD="$JFROG_PASSWORD" \
  -t auth-2fa-poc-backend:local \
  backend

# 2) Ejecutar inyectando el .env (fuera de VCS) y montando las credenciales.
#    -p publica el puerto; el proceso escucha en $PORT (default 8080).
docker run --rm \
  --env-file ./backend/.env \
  -v /ruta/local/secrets:/secrets:ro \
  -p 8080:8080 \
  auth-2fa-poc-backend:local
```

Verifica disponibilidad: `GET http://localhost:8080/api/v1/health`.

**Graceful shutdown:** `uvicorn` corre como PID 1 dentro del contenedor, por lo
que recibe `SIGTERM` directamente de `docker stop` / Cloud Run y cierra de
forma ordenada (deja de aceptar conexiones nuevas, termina las en curso y
libera recursos). `docker stop auth-2fa-poc-backend` dispara este cierre.

**Portabilidad a Cloud Run (R9.2, R9.5):** se despliega **la misma imagen** (por
digest, sin reconstrucción). Cloud Run inyecta `PORT` y el resto de variables /
secretos desde Secret Manager; el contenedor las lee igual que en local.

### Opción B — uvicorn con Poetry (desarrollo con reload)

Para iteración rápida sin construir la imagen. Carga las variables del `.env`
en tu shell antes de ejecutar (o usa tu gestor de entorno preferido).

```bash
poetry install
poetry run uvicorn main:app --reload --app-dir src --port "${PORT:-8080}"
```
