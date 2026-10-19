# Frontend_Angular — PoC Autenticacion 2FA

Aplicacion Angular que presenta el ingreso identifier-first, el prompt de 2FA y
el dashboard protegido de la PoC.

## Stack

- Angular 20 (standalone, `inject()`, `HttpClient`, RxJS).
- Firebase Client SDK (`firebase`) para la autenticacion directa.
- Jest como runner de pruebas unitarias.

Todas las dependencias se fijan a version exacta y se resuelven desde el
registro institucional JFrog (ver `.npmrc`).

## Configuracion por entorno (R9.6, R9.7)

La URL del Backend_FastAPI **no** se embebe en el codigo. Se resuelve en tiempo
de ejecucion desde `window.__APP_CONFIG__.apiBaseUrl`, provisto por el entorno
de hosting (local o Firebase Hosting en GCP). No existe un valor por defecto:
si la URL no esta resuelta, el `AuthService` bloquea las llamadas y expone un
error de configuracion.

El bundle Angular es **identico entre entornos**; solo cambia un archivo
`config.js` servido por el hosting. El build copia `src/config.template.js` al
output como `config.js` (ver `angular.json` -> `assets`) y `index.html` lo carga
como script **externo del mismo origen** antes del bundle (compatible con la CSP
`script-src 'self'`, que bloquea scripts inline).

`config.template.js` trae el marcador `__API_BASE_URL__`. En el despliegue se
sustituye por la URL real del backend del entorno (incluido el prefijo
`/api/v1`). Si no se sustituye, `resolveApiBaseUrl()` lo interpreta como "no
resuelta" (cadena vacia): **no hay direccion por defecto embebida** (R9.7).

Sustitucion en el despliegue (ejemplo con `envsubst`, p. ej. en CI/CD):

```bash
API_BASE_URL="https://<backend-cloud-run>/api/v1" \
  envsubst < src/config.template.js > dist/auth-2fa-poc-frontend/browser/config.js
```

El resultado servido por el hosting:

```js
window.__APP_CONFIG__ = { apiBaseUrl: 'https://<backend-del-entorno>/api/v1' };
```

## Despliegue en Firebase Hosting (GCP) — R9.5

El build de produccion usa el application builder de Angular, que emite los
artefactos del navegador en `dist/auth-2fa-poc-frontend/browser`. `firebase.json`
apunta su `public` a esa carpeta, con reescritura SPA de `**` a `/index.html`.

Pasos de despliegue:

```bash
npm run build:prod                                   # genera dist/.../browser
# sustituir el config.js del entorno (ver seccion anterior)
firebase deploy --only hosting --project <PROJECT_ID>
```

`.firebaserc` incluye un placeholder `REEMPLAZAR_CON_PROJECT_ID_FIREBASE`.
Reemplacelo por el project id real de Firebase/GCP (o use `--project` en el
comando de deploy). El id lo aprovisiona el responsable del despliegue.

### Headers de seguridad (hosting) — steering + R10

`firebase.json` fija los headers de seguridad obligatorios para todas las rutas:

| Header | Valor | Proposito |
|---|---|---|
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains; preload` | HSTS: fuerza HTTPS |
| `Content-Security-Policy` | `script-src 'self'` (bloquea inline), `object-src 'none'`, `frame-ancestors 'none'`, `upgrade-insecure-requests`, ... | Mitiga XSS/clickjacking |
| `X-Content-Type-Options` | `nosniff` | Evita MIME sniffing |
| `X-Frame-Options` | `DENY` | Anti-clickjacking |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Limita fuga de referrer |
| `Permissions-Policy` | APIs sensibles deshabilitadas | Reduce superficie |
| `Cross-Origin-Opener-Policy` | `same-origin` | Aisla el contexto de navegacion |

La CSP mantiene `script-src 'self'` (bloquea scripts inline, por eso `config.js`
es un script externo del mismo origen). `connect-src 'self' https:` permite que
el frontend alcance el backend (Cloud Run) resuelto por entorno; en despliegues
endurecidos puede restringirse al origen exacto del backend una vez conocido.

`config.js` e `index.html` se sirven con `Cache-Control: no-store` para que un
cambio de entorno (URL del backend) tome efecto sin cache; los assets con hash
(`*.js`, `*.css`, imagenes) se cachean de forma inmutable.

## Rutas

| Ruta        | Componente            | Descripcion                     |
|-------------|-----------------------|---------------------------------|
| `login`     | `LoginComponent`      | Ingreso identifier-first        |
| `2fa`       | `TwoFactorComponent`  | Prompt de segundo factor (TOTP) |
| `dashboard` | `DashboardComponent`  | Recurso protegido               |

## Instalacion y ejecucion

Configure las variables del registro JFrog en el entorno antes de instalar:

```bash
# JFROG_NPM_REGISTRY, JFROG_NPM_REGISTRY_HOST, JFROG_NPM_AUTH_TOKEN
npm ci
npm start        # servidor de desarrollo
npm run build    # build de produccion
npm test         # pruebas unitarias (Jest)
```

## Autores

Equipo PoC Autenticacion 2FA — Seguros Bolivar.
