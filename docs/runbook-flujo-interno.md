# Runbook — Dejar listo el flujo Interno (SAML → F5 APM)

> Estado: **el código está completo y parametrizado**. Este runbook es la lista
> de pasos de **configuración** que se ejecutan cuando el administrador de F5 APM
> entregue los metadatos del IdP. No requiere cambios de código: solo pegar
> valores y crear el proveedor SAML en Identity Platform.

## 0. Qué ya está listo (no tocar)

El flujo Interno ya está implementado de extremo a extremo; solo falta la
configuración del proveedor:

- **Backend:**
  - `classify_route` enruta un identificador de 5–15 dígitos a
    `INTERNO` / `saml_redirect`.
  - `verify_id_token` + `derive_user_type` reconocen el proveedor
    `firebase.sign_in_provider = "saml.<id>"` y lo mapean a `UserType.INTERNO`.
  - `/auth/session`, `/dashboard` y `/auth/logout` son agnósticos del proveedor:
    funcionan igual para Cliente e Interno.
- **Frontend:**
  - `AuthService.signInInterno()` dispara
    `signInWithRedirect(SAMLAuthProvider(samlProviderId))`.
  - `AuthService.completeRedirectSignIn()` maneja el retorno con
    `getRedirectResult`.
  - El `samlProviderId` se resuelve por configuración (sin hardcodear).
- **Config parametrizada (hoy con placeholder):**
  - Backend: `backend/.env` → `SAML_PROVIDER_ID=saml.f5apm-pendiente`.
  - Frontend: `frontend/src/config.js` → `samlProviderId: 'saml.f5apm-pendiente'`.

## 0.b Datos reales recibidos de F5 APM (metadata verificada)

> Metadatos del IdP ya entregados y verificados contra el XML del
> `<EntityDescriptor>`. **Estos son los valores que se usan en el Paso 2.**

| Dato | Valor real |
|---|---|
| Provider ID (nuestro) | `saml.f5apm` (ya cableado en `backend/.env` y `frontend/src/config.js`) |
| IdP Entity ID | `https://ambientepruebas.segurosbolivar.com/napm/saml2/poc-firebase` |
| IdP SSO URL | `https://ambientepruebas.segurosbolivar.com/saml/idp/profile/redirectorpost/sso` |
| Certificado de firma | `C:\CarpetaDeTrabajo\PoC Auth2FA\idp-signing.pem` (CN=`saml-idp-segbol`, válido hasta 2036-05-18) |
| SP Entity ID | `https://sandbox-arquitectura.firebaseapp.com` |
| ACS / callback | `https://sandbox-arquitectura.firebaseapp.com/__/auth/handler` |
| Firma | Aserción firmada; **sin** AuthnRequest firmado → `signRequest: false` |

> **Discrepancia 1 — Entity ID (resuelta):** el correo mencionaba
> `https://ambientepruebas.segurosbolivar.com/napm/saml2`, pero el `entityID`
> real del metadata XML es
> `https://ambientepruebas.segurosbolivar.com/napm/saml2/poc-firebase` (con
> `/poc-firebase`). Se usa el del XML, que es la fuente de verdad y debe coincidir
> con el `<Issuer>` de la aserción.
>
> **Discrepancia 2 — NameID (a validar en la prueba):** el SP pidió NameID
> `emailAddress`, pero el XML declara `nameid-format:persistent`. El correo del
> usuario llega como **atributo** (`.../claims/mail`), no como NameID. En la
> prueba real hay que confirmar que Identity Platform expone el correo (claim
> `email`) a partir del atributo; si no, el dashboard mostrará el identificador
> persistente en lugar del correo. No bloquea el login.

> **Script listo:** el comando `curl` de creación del proveedor (con el PEM ya
> escapado y el Entity ID correcto) está generado en
> `C:\CarpetaDeTrabajo\PoC Auth2FA\crear-proveedor-saml.sh`. Ábralo, copie su
> contenido y péguelo en **Cloud Shell** (incluye además un `GET` de
> verificación). **No commitear ese archivo:** contiene el certificado del IdP.

## 1. Datos que deben llegar del administrador de F5 APM

| Dato | Formato | Dónde se usa |
|---|---|---|
| Entity ID del IdP | URI / cadena | Proveedor SAML en Identity Platform |
| SSO URL del IdP | URL **HTTPS** | Proveedor SAML en Identity Platform |
| Certificado de firma (token-signing) | **PEM** (con `BEGIN/END CERTIFICATE`) | Proveedor SAML en Identity Platform |

Datos que ya definimos nosotros (SP), ya enviados a F5 APM:

- **ACS URL / callback:** `https://sandbox-arquitectura.firebaseapp.com/__/auth/handler`
- **SP Entity ID / audience:** `https://sandbox-arquitectura.firebaseapp.com`
- **NameID** del usuario con formato `emailAddress` (o el identificador de mapeo).

## 2. Crear el proveedor SAML en Identity Platform (dentro del TENANT)

> **Importante:** el proveedor debe crearse **dentro del tenant**
> `poc-auth-2fa-vh5h3`, no a nivel de proyecto. En la consola, seleccione primero
> el tenant en el selector de tenants.

### Opción A — Consola (recomendada)

1. Consola de GCP → **Identity Platform** → selector de tenants →
   **`poc-auth-2fa-vh5h3`**.
2. **Identity providers → Add a Provider → SAML**.
3. Rellenar con los datos del IdP (F5 APM):
   - **Name:** `F5 APM Corporativo` (o el que prefiera).
   - **Provider ID:** pulsar **Edit** y fijarlo; **debe empezar con `saml.`**
     (p. ej. `saml.f5apm`). **Anote este valor exacto** — es el que va en la
     config del backend y frontend (Paso 3).
   - **Entity ID (IdP):** el Entity ID de F5 APM.
   - **SAML SSO URL:** la SSO URL de F5 APM (HTTPS).
   - **Certificate:** el certificado PEM de F5 APM (incluyendo las líneas
     `-----BEGIN/END CERTIFICATE-----`).
4. Sección **Service provider:**
   - **Entity ID (SP):** `https://sandbox-arquitectura.firebaseapp.com`.
   - Confirmar que el **Authorization callback** es
     `https://sandbox-arquitectura.firebaseapp.com/__/auth/handler`.
5. **Save**.
6. **Resultado observable:** el proveedor aparece en **Identity providers** del
   tenant con su `saml.<id>` y estado habilitado.

### Opción B — Cloud Shell (REST API)

Si prefiere no usar la consola, cree el proveedor SAML del tenant vía REST API.
Reemplace los `<...>` por los valores reales:

```bash
curl -X POST "https://identitytoolkit.googleapis.com/v2/projects/sandbox-arquitectura/tenants/poc-auth-2fa-vh5h3/inboundSamlConfigs?inboundSamlConfigId=saml.f5apm" \
    -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    -H "Content-Type: application/json" \
    -H "X-Goog-User-Project: sandbox-arquitectura" \
    -d '{
      "displayName": "F5 APM Corporativo",
      "idpConfig": {
        "idpEntityId": "<APM_ENTITY_ID>",
        "ssoUrl": "<APM_SSO_URL>",
        "signRequest": false,
        "idpCertificates": [{ "x509Certificate": "<APM_CERT_PEM>" }]
      },
      "spConfig": {
        "spEntityId": "https://sandbox-arquitectura.firebaseapp.com",
        "callbackUri": "https://sandbox-arquitectura.firebaseapp.com/__/auth/handler"
      }
    }'
```

- El `inboundSamlConfigId` **debe empezar con `saml.`**; ése es el Provider ID.
  Reglas del nombre (API oficial): tras `saml.` debe iniciar con **letra
  minúscula**, terminar en carácter alfanumérico y tener al menos 2 caracteres
  (solo alfanuméricos, guiones, guiones bajos o puntos). Ej. válido: `saml.f5apm`.
- `<APM_CERT_PEM>` debe ir como una sola cadena JSON (los saltos de línea del
  PEM escapados como `\n`).
- **Resultado observable:** la respuesta trae el `name` del proveedor y el
  `idpConfig`/`spConfig` reflejados.

## 3. Pegar el Provider ID en la configuración (único cambio local)

Sustituir el placeholder `saml.f5apm-pendiente` por el Provider ID real
(p. ej. `saml.f5apm`) en **dos** lugares:

- **Backend** — `backend/.env`:
  ```
  SAML_PROVIDER_ID=saml.f5apm
  ```
  (Reiniciar el backend para que tome el nuevo valor.)

- **Frontend** — `frontend/src/config.js`:
  ```js
  samlProviderId: 'saml.f5apm',
  ```
  (En producción, este valor lo inyecta el pipeline vía
  `__FIREBASE_SAML_PROVIDER_ID__` en `config.template.js`.)

> El valor del backend y el del frontend **deben coincidir** y ser idénticos al
> Provider ID creado en el Paso 2.

## 4. Autorizar dominios para el redirect

El redirect SAML vuelve al `authDomain` (`sandbox-arquitectura.firebaseapp.com`),
que ya está autorizado por defecto. Para **probar en local**, agregue `localhost`
a los dominios autorizados:

- Consola de Firebase → **Authentication → Settings → Authorized domains →
  Add domain** → `localhost`.
- **Resultado observable:** `localhost` aparece en la lista de dominios
  autorizados.

## 5. Probar el flujo Interno (en LOCAL, sin desplegar a GCP)

> No es necesario desplegar en GCP: F5 APM e Identity Platform intercambian la
> SAML Response a través de dominios de Firebase (públicos); el frontend y el
> backend pueden seguir en `localhost`. Solo se requiere que el proveedor SAML
> (Paso 2) y los dominios autorizados (Paso 4) estén configurados.

1. Backend y frontend arrancados en local (ver Sección 8 de la Guía Operativa).
2. En la pantalla de ingreso, escribir un **número de identificación de 5–15
   dígitos** → **Continuar**.
3. **Resultado observable:** el navegador redirige a la pantalla de **F5 APM**.
4. Autenticarse en F5 APM y completar el 2FA corporativo (OTP de F5).
5. **Resultado observable:** el navegador vuelve a la app; el frontend obtiene el
   ID token y establece la sesión; se muestra el **dashboard** con
   `user_type = Interno` y los claims enmascarados.

## 6. Bloqueos frecuentes (R8.7)

| Síntoma | Causa probable | Acción correctiva |
|---|---|---|
| No hay redirect a F5 APM | `samlProviderId` no coincide con el proveedor creado, o el proveedor no está en el tenant | Verificar Paso 2 y Paso 3 (mismo `saml.<id>` en ambos y dentro del tenant) |
| `auth/argument-error` al redirigir | El Provider ID no empieza por `saml.` | Reemitir el proveedor con un ID que empiece por `saml.` |
| Error de firma inválida/expirada | Certificado del IdP incorrecto o vencido | Pedir a F5 APM el certificado de firma vigente y actualizar el proveedor |
| `audience`/Entity ID no coincide | `spEntityId` distinto en Identity Platform y en F5 APM | Igualar `https://sandbox-arquitectura.firebaseapp.com` en ambos extremos |
| El redirect no vuelve (dominio no autorizado) | Falta `localhost` (o el dominio real) en Authorized domains | Agregarlo (Paso 4) |
| Dashboard `401` tras volver de F5 APM | La cookie no fluye (cross-site) | Usar el proxy de Angular y `apiBaseUrl` relativo (igual que el flujo Cliente) |

## 7. Checklist rápido

- [ ] Metadatos de F5 APM recibidos (Entity ID, SSO URL, certificado PEM)
- [ ] Proveedor SAML creado **en el tenant** `poc-auth-2fa-vh5h3` (Paso 2)
- [ ] Provider ID real pegado en `backend/.env` y `frontend/src/config.js` (Paso 3)
- [ ] Backend reiniciado con el nuevo `SAML_PROVIDER_ID`
- [ ] `localhost` autorizado en Authorized domains (Paso 4)
- [ ] F5 APM confirmó el registro del SP (ACS URL + SP Entity ID)
- [ ] Prueba de login Interno en local exitosa (Paso 5)

---

## 8. Hallazgos de la activacion real (verificado)

Durante la activacion con los metadatos reales de F5 APM se confirmaron y
resolvieron varios puntos. Quedan documentados para no repetir el diagnostico.

### 8.1 El proveedor debe quedar `enabled: true`

El `POST` de creacion deja el proveedor con `enabled` vacio (deshabilitado). Con
el proveedor deshabilitado, el handler de Firebase REBOTA de inmediato a
`/login` sin redirigir al IdP y sin mostrar error (sintoma: "no pasa nada"). Se
corrige con un `PATCH` sobre `?updateMask=enabled` enviando `{"enabled": true}`,
y se verifica con un `GET`. Ya aplicado en esta PoC.

### 8.2 El correo y el nombre llegan como ATRIBUTOS SAML, no como claims top-level

Identity Platform NO tiene "attribute mapping" para SAML. Segun la doc oficial,
el claim `email` del ID token solo se puebla si el correo viene en el `<NameID>`
con formato `emailAddress`. El IdP de F5 APM envia NameID `persistent` (un uid) y
el correo como atributo, de modo que el token expone los atributos crudos bajo
`firebase.sign_in_attributes` con las URIs completas:

- Correo: `http://schemas.xmlsoap.org/ws/2005/05/identity/claims/mail`
- Nombre: `http://schemas.xmlsoap.org/ws/2005/05/identity/claims/fullName`
  (o `givenname` + `sn`)

Ajuste aplicado en el backend (`backend/src/dashboard/router.py`): el dashboard,
para la ruta Interno, lee el correo y el nombre desde `firebase.sign_in_attributes`
cuando no vienen como claims top-level. El correo se enmascara igual que en la
ruta Cliente (R6.4). La ruta Cliente no cambia. Se agrego una prueba unitaria.

### 8.3 El redirect en local requiere un origen IPv4 autorizado

El redirect de Firebase (`signInWithRedirect` / `getRedirectResult`) se cuelga
SIN error cuando el dev server queda escuchando solo en IPv6 (`[::1]`). Firebase
Auth no trata `[::1]` como un origen valido para el flujo de redirect. Solucion:

1. Arrancar el dev server forzando IPv4 con el flag de host `127.0.0.1`.
2. Acceder por `http://127.0.0.1:4200` (no `localhost`, que puede resolver a IPv6).
3. `127.0.0.1` debe estar en los dominios autorizados de Firebase Auth (ademas de
   `localhost`). Ya agregado en esta PoC.

### 8.4 Manejo del retorno del redirect en el `LoginComponent`

El retorno del IdP aterriza en `/login` (la pantalla que inicio el redirect), no
en `/2fa`. Se agrego a `LoginComponent.ngOnInit` el consumo del resultado con
`completeRedirectSignIn` (`getRedirectResult`), SIN bloquear ni ocultar el
formulario de ingreso: si vuelve un usuario del IdP, completa la sesion y navega
al dashboard; si no hay redirect pendiente, la pantalla de ingreso sigue usable.