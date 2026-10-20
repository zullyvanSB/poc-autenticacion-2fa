# Runbook — Dejar listo el camino B (login corporativo con Google)

> Estado: **el código está completo y parametrizado** (cadena POCA-001→007).
> Este runbook es la lista de pasos de **configuración** que ejecuta el operador
> para dejar operativo el login corporativo con Google. No requiere cambios de
> código: solo habilitar el proveedor en el tenant, autorizar los dominios y
> fijar dos valores de configuración (uno en el backend, uno en el frontend).

## 0. Qué ya está listo (no tocar)

El camino B ya está implementado de extremo a extremo; solo falta la
configuración del proveedor y de la allowlist:

- **Backend:**
  - `get_corporate_allowed_domains` (`common/config.py`) lee la variable
    `CORPORATE_ALLOWED_DOMAINS` (lista separada por comas), recorta, descarta
    vacíos y normaliza a minúsculas; es **fail-safe** (ausente/vacía ⇒ `[]`). No
    forma parte de `REQUIRED_ENV_VARS` (es opcional).
  - La validación server-side del camino B (POCA-004) autoriza una sesión de
    Google solo si el dominio del correo del token (la parte tras el `@`)
    **coincide exactamente** con uno de la allowlist. Con la allowlist vacía,
    rechaza todo token de Google (deny por defecto).
  - `/auth/session`, `/dashboard` y `/auth/logout` son agnósticos del proveedor:
    funcionan igual para Cliente, Interno (SAML) y corporativo (Google).
- **Frontend:**
  - `AuthService.signInGoogle()` dispara el login con Google
    (`signInWithPopup` sobre `GoogleAuthProvider`) y aplica el dominio
    corporativo como parámetro `hd` vía `setCustomParameters({ hd })`.
  - El `corporateHostedDomain` (el `hd`) se resuelve por runtime-config
    (`runtime-config.ts`), sin hardcodear en código TS.
- **Config parametrizada:**
  - Backend: `backend/.env` → `CORPORATE_ALLOWED_DOMAINS=...` (ver
    `backend/README.md`, subsección de variables opcionales).
  - Frontend: `frontend/src/config.js` → `corporateHostedDomain: 'segurosbolivar.com'`
    (desarrollo local); en despliegue el pipeline sustituye el marcador
    `__FIREBASE_CORPORATE_HOSTED_DOMAIN__` de `frontend/src/config.template.js`.

> **Clave de seguridad (leer antes de configurar):** el `hd` del frontend es
> **cosmético** — solo filtra el selector de cuenta de Google para mostrar las
> del dominio indicado. **La autorización real de dominio es server-side**
> (POCA-004, vía `CORPORATE_ALLOWED_DOMAINS`). Configurar **solo** el `hd` sin
> `CORPORATE_ALLOWED_DOMAINS` deja el camino B inoperante server-side: el popup
> puede dispararse, pero la sesión se rechaza con `401` (fail-safe deny). **Hay
> que configurar ambos lados.**

## 1. Alcance de dominios aprobado

| Dato | Valor |
|---|---|
| Dominios corporativos permitidos | `@segurosbolivar.com` y `@serviciosbolivar.com` |
| Dominio NO permitido (fuera de alcance) | `samtel.eu` (no se autoriza ni en el proveedor ni en la allowlist) |
| Semántica de match | **exacta** sobre el dominio (no subdominios): `mail.segurosbolivar.com` **no** coincide |
| `hd` primario (frontend, cosmético) | `segurosbolivar.com` (un único dominio; la validación multi-dominio real es server-side) |
| Tenant de Identity Platform | `poc-auth-2fa-vh5h3` |

> El `hd` de Google admite un **único** dominio primario (cosmético). La
> autorización de los **dos** dominios se hace server-side con
> `CORPORATE_ALLOWED_DOMAINS=segurosbolivar.com,serviciosbolivar.com`.

## 2. Habilitar el proveedor Google dentro del TENANT

> **Importante:** el proveedor debe habilitarse **dentro del tenant**
> `poc-auth-2fa-vh5h3`, no a nivel de proyecto. En la consola, seleccione primero
> el tenant en el selector de tenants (igual que para el proveedor SAML del
> camino A).

1. Consola de GCP → **Identity Platform** → selector de tenants →
   **`poc-auth-2fa-vh5h3`**.
2. **Identity providers → Add a Provider → Google**.
3. Habilitar el proveedor **Google** y guardar (**Save**). Si la consola pide un
   **Web client ID / secret** del proyecto, use los del proyecto
   `sandbox-arquitectura` (OAuth client del tipo "Web application"); para el
   flujo con el SDK web de Firebase dentro del tenant suele bastar con habilitar
   el proveedor.
4. **Resultado observable:** el proveedor **Google** aparece en **Identity
   providers** del tenant `poc-auth-2fa-vh5h3` con estado **habilitado**
   (`enabled: true`).

> **Nota (analogía con el camino A):** igual que el proveedor SAML, un proveedor
> recién creado puede quedar `enabled` vacío (deshabilitado). Si el login con
> Google "no hace nada" (rebota a `/login` sin abrir el popup), verifique que el
> proveedor quede `enabled: true` en el tenant.

## 3. Autorizar los dominios permitidos

Autorizar **solo** los dos dominios aprobados en la restricción de acceso del
proveedor Google / dominios de Firebase Auth del tenant (sin `samtel.eu`):

1. En la configuración del proveedor Google del tenant, en la restricción de
   dominios (o en **Authentication → Settings → Authorized domains** del
   tenant), autorizar `segurosbolivar.com` y `serviciosbolivar.com`.
2. **Resultado observable:** ambos dominios aparecen listados como permitidos; no
   aparece `samtel.eu`.

> Esta restricción del proveedor es complementaria, **no** sustituye la
> validación server-side. La autorización efectiva la impone el backend (Paso 4).

## 4. Configurar la allowlist server-side (backend) — `CORPORATE_ALLOWED_DOMAINS`

Fijar la allowlist en el entorno del backend (ver `backend/README.md`,
subsección "Variables opcionales por entorno"):

- **Backend** — `backend/.env` (local, fuera de VCS) o env del entorno de
  despliegue (Secret Manager / Cloud Run):
  ```dotenv
  CORPORATE_ALLOWED_DOMAINS=segurosbolivar.com,serviciosbolivar.com
  ```
  (Reiniciar el backend para que tome el nuevo valor.)

- **Resultado observable:** el backend arranca sin error; una sesión iniciada con
  una cuenta `@segurosbolivar.com` o `@serviciosbolivar.com` se acepta
  (`/auth/session` responde OK y el dashboard carga); una cuenta de otro dominio
  (`@gmail.com`, `samtel.eu`, un subdominio como `@mail.segurosbolivar.com`) se
  **rechaza** server-side (`401` genérico).

> **Fail-safe:** si omite esta variable (o la deja vacía), la allowlist queda
> vacía y **todo** token de Google se rechaza server-side. El valor es **solo el
> dominio** (sin `@`), en minúsculas, separados por comas, con **match exacto**.

## 5. Configurar el `hd` por runtime-config (frontend) — `corporateHostedDomain`

Proveer el dominio corporativo `hd` por la config de runtime (sin hardcodear en
código TS):

- **Local (desarrollo)** — `frontend/src/config.js`, bloque `firebase`:
  ```js
  corporateHostedDomain: 'segurosbolivar.com',
  ```
- **Despliegue** — `frontend/src/config.template.js` trae el marcador
  `corporateHostedDomain: '__FIREBASE_CORPORATE_HOSTED_DOMAIN__'`; el pipeline lo
  sustituye (p. ej. `FIREBASE_CORPORATE_HOSTED_DOMAIN="segurosbolivar.com"` con
  `envsubst`). Si el marcador no se sustituye, `resolveConfigString` lo trata
  como no resuelto y el campo no se incluye (fail-safe): el popup se dispara sin
  filtro de dominio, pero la autorización server-side (Paso 4) sigue aplicando.

- **Resultado observable:** al iniciar el login con Google, el selector de cuenta
  de Google aparece **prefiltrado** al dominio `segurosbolivar.com` (cosmético).

## 6. Probar el camino B (en LOCAL)

1. Backend y frontend arrancados en local (ver Sección 8 de la Guía Operativa),
   con `CORPORATE_ALLOWED_DOMAINS` (Paso 4) y `corporateHostedDomain` (Paso 5)
   configurados.
2. En la pantalla de ingreso, iniciar el camino corporativo (Google).
3. **Resultado observable:** se abre el **popup** de Google (no redirect),
   prefiltrado al dominio del `hd`.
4. Autenticarse con una cuenta `@segurosbolivar.com` o `@serviciosbolivar.com`.
5. **Resultado observable:** el frontend obtiene el ID token, el backend valida el
   dominio contra la allowlist y establece la sesión; se muestra el **dashboard**
   con los claims enmascarados. Una cuenta de un dominio no permitido es
   rechazada server-side (`401`) aunque el popup se haya abierto.

## 7. Bloqueos frecuentes (R8.7)

| Síntoma | Causa probable | Acción correctiva |
|---|---|---|
| El popup de Google "no hace nada" / rebota a `/login` | Proveedor Google deshabilitado en el tenant (`enabled` vacío) | Verificar Paso 2: el proveedor debe quedar `enabled: true` **en el tenant** `poc-auth-2fa-vh5h3` |
| El login abre el popup pero la sesión da `401` | `CORPORATE_ALLOWED_DOMAINS` ausente/vacía, o el dominio de la cuenta no está en la lista (fail-safe deny) | Configurar la allowlist (Paso 4) con los dos dominios; reiniciar el backend. El `hd` solo es cosmético |
| Entra una cuenta que no debería (dominio no permitido) | Se confió en el `hd` del frontend como control de seguridad | El `hd` NO autoriza; la autorización es server-side (Paso 4). Revisar que `CORPORATE_ALLOWED_DOMAINS` liste **solo** los dos dominios aprobados |
| El selector de Google no filtra por dominio | `corporateHostedDomain` no resuelto (ausente en `config.js` o marcador sin sustituir) | Fijar `corporateHostedDomain` (Paso 5). No bloquea el login; la autorización real sigue server-side |
| Dashboard `401` tras volver del popup | La cookie de sesión no fluye (cross-site) | Usar el proxy de Angular y `apiBaseUrl` relativo (mismo hostname front/back); acceder por `127.0.0.1` si `localhost` resuelve a IPv6 (igual que el camino A) |
| Subdominio rechazado (`@mail.segurosbolivar.com`) | Match **exacto** por diseño (no subdominios) | Es el comportamiento esperado; autorizar el dominio exacto si se requiere, coherente con POCA-002/004 |

## 8. Checklist rápido

- [ ] Proveedor **Google** habilitado **en el tenant** `poc-auth-2fa-vh5h3`, `enabled: true` (Paso 2)
- [ ] Dominios `segurosbolivar.com` y `serviciosbolivar.com` autorizados; **sin** `samtel.eu` (Paso 3)
- [ ] `CORPORATE_ALLOWED_DOMAINS=segurosbolivar.com,serviciosbolivar.com` en el backend (Paso 4)
- [ ] Backend reiniciado con la nueva allowlist
- [ ] `corporateHostedDomain` resuelto por runtime-config (`config.js` local / marcador sustituido en despliegue) (Paso 5)
- [ ] Prueba de login con Google en local exitosa, y rechazo verificado para un dominio no permitido (Paso 6)
