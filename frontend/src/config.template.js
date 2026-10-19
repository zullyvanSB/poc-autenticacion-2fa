/*
 * Plantilla de configuracion de runtime del Frontend_Angular (R9.6, R9.7).
 *
 * El entorno de hosting SUSTITUYE este archivo en el despliegue por uno con la
 * URL real del Backend_FastAPI y la configuracion web PUBLICA de Firebase del
 * entorno correspondiente (local / GCP). El artefacto de build (bundle Angular)
 * es identico entre entornos: solo cambia este config.js, servido por Firebase
 * Hosting sin cache (ver firebase.json).
 *
 * NO se embebe ninguna URL ni configuracion por defecto: si los marcadores no
 * se sustituyen, el `AuthService` detecta la configuracion no resuelta, bloquea
 * las llamadas al backend y no inicializa el SDK de Firebase, exponiendo un
 * error de configuracion (R9.7).
 *
 * Los valores de `firebase` son configuracion PUBLICA del cliente web (no
 * secretos): Firebase los expone en el navegador por diseno. Los secretos
 * (cuenta de servicio del Admin SDK, config OIDC/SAML del backend) permanecen
 * exclusivamente en el servidor (R5.2) y NUNCA se colocan aqui.
 *
 * Para generar el config.js del entorno en el despliegue, sustituir los
 * marcadores __..._ por los valores reales, por ejemplo con envsubst o un paso
 * del pipeline de CI/CD:
 *
 *   API_BASE_URL="https://<backend-cloud-run>/api/v1" \
 *   FIREBASE_API_KEY="..." FIREBASE_AUTH_DOMAIN="<proj>.firebaseapp.com" \
 *   FIREBASE_PROJECT_ID="<proj>" FIREBASE_SAML_PROVIDER_ID="saml.f5-apm" \
 *     envsubst < src/config.template.js > dist/auth-2fa-poc-frontend/browser/config.js
 *
 * Los marcadores se dejan sin sustituir a proposito: un despliegue sin
 * sustitucion NO obtiene valores funcionales embebidos.
 */
window.__APP_CONFIG__ = {
  apiBaseUrl: '__API_BASE_URL__',
  firebase: {
    apiKey: '__FIREBASE_API_KEY__',
    authDomain: '__FIREBASE_AUTH_DOMAIN__',
    projectId: '__FIREBASE_PROJECT_ID__',
    samlProviderId: '__FIREBASE_SAML_PROVIDER_ID__',
    tenantId: '__FIREBASE_TENANT_ID__',
    appId: '__FIREBASE_APP_ID__'
  }
};
