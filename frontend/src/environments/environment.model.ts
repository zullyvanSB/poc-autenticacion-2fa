/**
 * Configuracion web PUBLICA de Firebase (Client SDK).
 *
 * Estos valores son configuracion publica del cliente Firebase (apiKey web,
 * authDomain, projectId, etc.), NO secretos: Firebase los expone en el bundle
 * del navegador por diseno y la seguridad real recae en las reglas del lado
 * del servidor y en la verificacion del ID token por el backend (R5.2, R5.3).
 *
 * Aun asi, se resuelven por entorno (R9.6) y NO se embeben en el codigo fuente:
 * el hosting los provee en `window.__APP_CONFIG__.firebase`. Si no estan
 * resueltos, el `AuthService` impide inicializar el SDK y expone un error de
 * configuracion (R9.7), sin recurrir a valores por defecto embebidos.
 */
export interface FirebaseWebConfig {
  readonly apiKey: string;
  readonly authDomain: string;
  readonly projectId: string;
  /** Nombre del proveedor SAML configurado en Firebase para Internos (p. ej. `saml.f5-apm`). */
  readonly samlProviderId: string;
  /**
   * ID del tenant de Identity Platform que aisla a los usuarios de la PoC en un
   * tenant dedicado, separado del nivel de proyecto compartido (R: aislamiento
   * en tenant dedicado). Es opcional: si no se resuelve, la app sigue operando
   * a nivel de proyecto; cuando esta presente, el SDK debe operar dentro de
   * ese tenant (sign-in, MFA/TOTP y SAML quedan confinados al tenant).
   */
  readonly tenantId?: string;
  readonly appId?: string;
  readonly storageBucket?: string;
  readonly messagingSenderId?: string;
}

/**
 * Contrato de configuracion por entorno del Frontend_Angular.
 *
 * La URL del Backend_FastAPI se resuelve exclusivamente por entorno (R9.6):
 * no se embeben direcciones IP ni URLs estaticas en el codigo fuente, ni un
 * valor por defecto (R9.7). Si `apiBaseUrl` queda vacio, el `AuthService`
 * debe impedir las llamadas al backend y exponer un error de configuracion.
 */
export interface AppEnvironment {
  /** `true` en builds de desarrollo/local; `false` en produccion (GCP). */
  readonly production: boolean;
  /**
   * URL base del Backend_FastAPI, incluido el prefijo de version (`/api/v1`).
   * Cadena vacia significa "no resuelta": el frontend NO debe inventar una
   * direccion por defecto (R9.7).
   */
  readonly apiBaseUrl: string;
  /**
   * Configuracion web publica de Firebase resuelta por entorno (R9.6).
   * `null` significa "no resuelta": el `AuthService` NO inicializa el SDK y
   * expone un error de configuracion (R9.7), sin valores por defecto embebidos.
   */
  readonly firebase: FirebaseWebConfig | null;
}
