# Desviaciones solo-local — Revertir antes de publicar

> Propósito: registrar **todos** los cambios hechos únicamente para poder
> ejecutar la PoC en la máquina local. Cada uno debilita o aparta una regla
> corporativa (gobierno IT, seguridad de cookies, CORS) y **debe revertirse
> antes de publicar / desplegar a un entorno real (Stage/Prod)**.
>
> Antes de abrir un PR hacia un entorno compartido, recorre esta lista y marca
> cada punto como revertido.

## Checklist de reversión

- [ ] 1. Restaurar `.npmrc` corporativo (JFrog) en el frontend
- [ ] 2. Backend: `APP_ENV=production` (cookie `Secure` + `SameSite=Strict`, sin CORS abierto)
- [ ] 3. Sustituir `config.js` del frontend por el generado desde `config.template.js` en el pipeline
- [ ] 4. Quitar/ignorar el proxy de desarrollo (`proxy.conf.json`) en el build de producción
- [ ] 5. Confirmar que no se publican credenciales ni valores reales locales
- [ ] 6. (Revisar) pin de `zone.js` y puerto del backend

---

## 1. `.npmrc` del frontend apunta a npm público (debería ser JFrog)

- **Qué se cambió:** `frontend/.npmrc` apunta a `https://registry.npmjs.org/`
  para poder instalar dependencias en local (JFrog no es alcanzable desde esta
  máquina).
- **Estado corporativo guardado en:** `frontend/.npmrc.jfrog.bak` (resuelve
  desde JFrog vía `JFROG_NPM_REGISTRY` / `JFROG_NPM_AUTH_TOKEN`, con `audit=true`
  y `engine-strict=true`).
- **Regla que se incumple:** gobierno IT — todas las dependencias deben
  resolverse desde el repositorio institucional JFrog.
- **Cómo revertir:** reemplazar el contenido de `frontend/.npmrc` por el de
  `frontend/.npmrc.jfrog.bak`.
  - Para seguir instalando en local tras revertir, usar un override puntual sin
    tocar el archivo del repo, p. ej.:
    `npm install --registry=https://registry.npmjs.org/`.

## 2. Backend configurado como entorno local (`APP_ENV=local`)

- **Qué se cambió:** en `backend/.env` → `APP_ENV=local` y
  `CORS_ALLOWED_ORIGINS=http://localhost:4200`.
- **Efecto (solo cuando `APP_ENV=local`):**
  - La cookie de sesión se emite **no-`Secure` + `SameSite=Lax`** (para que
    fluya sobre HTTP plano en local).
  - Se habilita `CORSMiddleware` con los orígenes de desarrollo.
- **Comportamiento fail-safe:** cualquier valor de `APP_ENV` distinto de `local`
  (incluido ausente) se interpreta como **producción**: cookie `Secure` +
  `SameSite=Strict` y sin CORS abierto. No hay que cambiar código, solo la
  variable de entorno.
- **Regla que se incumple en local:** seguridad de cookies (Secure/SameSite) y
  CORS restrictivo.
- **Cómo revertir:** en el entorno real, definir `APP_ENV=production` (o
  simplemente no definir `APP_ENV=local`) y dejar `CORS_ALLOWED_ORIGINS` vacío o
  con el dominio real. No publicar el `.env` local.

## 3. `config.js` del frontend con valores locales embebidos

- **Qué se cambió:** `frontend/src/config.js` contiene valores concretos para
  local: `apiBaseUrl: '/api/v1'` (relativo, para el proxy) y la config web
  pública de Firebase del tenant de la PoC.
- **Estado correcto:** el artefacto de build debe ser idéntico entre entornos;
  solo cambia `config.js`, que se **genera en el despliegue** sustituyendo los
  marcadores de `frontend/src/config.template.js` (`__API_BASE_URL__`, etc.).
- **Nota:** los valores de `firebase` son **configuración pública** del cliente
  web (no secretos). Aun así, no deben quedar embebidos en el repo como estado
  de producción; se inyectan por entorno.
- **Cómo revertir:** no publicar `config.js` con valores locales; que el
  pipeline genere `config.js` desde la plantilla (p. ej. con `envsubst`) hacia
  `dist/.../browser/config.js`. Servir sin caché (ya está en `firebase.json`).

## 4. Proxy de desarrollo de Angular (`proxy.conf.json`)

- **Qué es:** `frontend/proxy.conf.json` reenvía `/api` →
  `http://localhost:8081`, de modo que el navegador ve backend y frontend en el
  **mismo origen** (`localhost:4200`) y la cookie de sesión fluye sin problemas
  de `SameSite` cross-site. Se usa solo con `ng serve`.
- **Regla/impacto:** es exclusivo de desarrollo; en producción el frontend
  (Firebase Hosting) llama al backend por su URL real resuelta en `config.js`.
- **Cómo revertir:** nada que desplegar; asegurarse de que el proxy **no**
  participa en el build de producción (`ng build` no lo usa). Mantenerlo en el
  repo solo como utilidad de desarrollo.

## 5. Credenciales y valores reales usados en local

- **Qué hay en local (NO publicar):**
  - `backend/.env` (gitignored) con `GOOGLE_APPLICATION_CREDENTIALS` apuntando a
    la clave JSON de la cuenta de servicio, `FIREBASE_PROJECT_ID`,
    `FIREBASE_TENANT_ID`, etc.
  - El archivo JSON de la cuenta de servicio vive **fuera del repo**
    (en `C:\CarpetaDeTrabajo\PoC Auth2FA\...`).
  - El usuario de prueba de la PoC es descartable.
- **Cómo revertir / al publicar:** nunca commitear `.env` ni la clave de
  servicio; en el entorno real, inyectar secretos vía Secret Manager / variables
  de entorno del servicio, no en archivos del repo.

## 6. Puntos menores a revisar

- **`zone.js` fijado en `0.15.1`** (en vez de 0.16.x) por incompatibilidad con
  Angular 20 en esta instalación local. Revisar si en el entorno/registro
  corporativo corresponde otra versión compatible pin-exacta.
- **Puerto del backend `8081`** en local porque el `8080` está ocupado en esta
  máquina. En el entorno real se usa el puerto que defina el servicio
  (Cloud Run inyecta `PORT`). El `.env` local trae `PORT=8080` pero el backend
  se arrancó en `8081`; conviene alinear ese valor en local.