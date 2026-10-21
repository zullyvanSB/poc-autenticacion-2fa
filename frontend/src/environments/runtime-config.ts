import type { AppEnvironment, FirebaseWebConfig } from './environment.model';

/**
 * Configuracion inyectada en tiempo de ejecucion por el entorno de hosting.
 *
 * El artefacto de build es identico entre local y GCP (R9.2 analogo para el
 * frontend); la URL del backend y la configuracion web publica de Firebase se
 * proveen por entorno sin recompilar y sin valores por defecto embebidos
 * (R9.6, R9.7). El hosting define `window.__APP_CONFIG__` (p. ej. mediante un
 * `config.js` servido por Firebase Hosting / Nginx con los valores del entorno
 * correspondiente).
 */
interface RuntimeAppConfig {
  readonly apiBaseUrl?: string;
  readonly firebase?: Partial<FirebaseWebConfig>;
}

declare global {
  interface Window {
    __APP_CONFIG__?: RuntimeAppConfig;
  }
}

/**
 * Marcador de la plantilla `config.template.js` que el hosting sustituye en el
 * despliegue (ver src/config.template.js y firebase.json). Si el despliegue no
 * realiza la sustitucion, el valor llega sin sustituir: se trata como "no
 * resuelta" para NO exponer una direccion por defecto embebida (R9.7).
 */
const UNSUBSTITUTED_PLACEHOLDER = '__API_BASE_URL__';

/**
 * Lee la URL base del backend desde la configuracion de runtime.
 * Devuelve cadena vacia si no esta resuelta: NO retorna una direccion por
 * defecto embebida (R9.7). La validacion/bloqueo de llamadas es responsabilidad
 * del `AuthService`.
 *
 * Un marcador de plantilla sin sustituir (`__API_BASE_URL__`) se interpreta
 * como "no resuelta" (cadena vacia), evitando que un despliegue mal
 * configurado use un valor no funcional como si fuera una URL valida.
 *
 * @returns la URL base resuelta por entorno, o cadena vacia si no existe.
 */
export function resolveApiBaseUrl(): string {
  const config = typeof window !== 'undefined' ? window.__APP_CONFIG__ : undefined;
  const url = config?.apiBaseUrl;
  if (typeof url !== 'string') {
    return '';
  }
  const trimmed = url.trim();
  return trimmed === UNSUBSTITUTED_PLACEHOLDER ? '' : trimmed;
}

/**
 * Normaliza un campo de texto de la configuracion de runtime: devuelve el
 * valor recortado solo si es una cadena no vacia y sin marcador de plantilla
 * sin sustituir; en cualquier otro caso devuelve cadena vacia.
 *
 * @param value valor crudo leido de `window.__APP_CONFIG__`.
 * @returns la cadena resuelta, o cadena vacia si no esta resuelta.
 */
function resolveConfigString(value: unknown): string {
  if (typeof value !== 'string') {
    return '';
  }
  const trimmed = value.trim();
  if (trimmed === '' || trimmed.startsWith('__') === true) {
    return '';
  }
  return trimmed;
}

/**
 * Lee la configuracion web publica de Firebase desde la configuracion de
 * runtime. Devuelve `null` si falta alguno de los campos obligatorios
 * (`apiKey`, `authDomain`, `projectId`, `samlProviderId`): el `AuthService` NO
 * inicializa el SDK y expone un error de configuracion (R9.7), sin recurrir a
 * valores por defecto embebidos. Los campos opcionales (`tenantId`,
 * `corporateHostedDomain` del camino B, `appId`, etc.) solo se incluyen cuando
 * estan resueltos y no forman parte del null-check de campos obligatorios.
 *
 * @returns el `FirebaseWebConfig` resuelto por entorno, o `null` si no existe.
 */
export function resolveFirebaseConfig(): FirebaseWebConfig | null {
  const config = typeof window !== 'undefined' ? window.__APP_CONFIG__ : undefined;
  const firebase = config?.firebase;
  if (firebase === undefined || firebase === null) {
    return null;
  }

  const apiKey = resolveConfigString(firebase.apiKey);
  const authDomain = resolveConfigString(firebase.authDomain);
  const projectId = resolveConfigString(firebase.projectId);
  const samlProviderId = resolveConfigString(firebase.samlProviderId);

  if (apiKey === '' || authDomain === '' || projectId === '' || samlProviderId === '') {
    return null;
  }

  const tenantId = resolveConfigString(firebase.tenantId);
  const corporateHostedDomain = resolveConfigString(firebase.corporateHostedDomain);
  const appId = resolveConfigString(firebase.appId);
  const storageBucket = resolveConfigString(firebase.storageBucket);
  const messagingSenderId = resolveConfigString(firebase.messagingSenderId);

  return {
    apiKey,
    authDomain,
    projectId,
    samlProviderId,
    ...(tenantId !== '' ? { tenantId } : {}),
    ...(corporateHostedDomain !== '' ? { corporateHostedDomain } : {}),
    ...(appId !== '' ? { appId } : {}),
    ...(storageBucket !== '' ? { storageBucket } : {}),
    ...(messagingSenderId !== '' ? { messagingSenderId } : {}),
  };
}

/**
 * Construye el objeto de entorno para un modo dado, resolviendo la URL del
 * backend y la configuracion de Firebase por entorno (sin defaults embebidos).
 *
 * @param production `true` para builds de produccion.
 * @returns el `AppEnvironment` con los valores resueltos por entorno.
 */
export function buildEnvironment(production: boolean): AppEnvironment {
  return {
    production,
    apiBaseUrl: resolveApiBaseUrl(),
    firebase: resolveFirebaseConfig(),
  };
}
