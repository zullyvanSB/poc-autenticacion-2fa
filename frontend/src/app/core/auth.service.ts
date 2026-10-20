import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, from, throwError } from 'rxjs';
import { timeout } from 'rxjs/operators';

import { type FirebaseApp, initializeApp } from 'firebase/app';
import {
  type Auth,
  type MultiFactorResolver,
  type MultiFactorUser,
  type TotpSecret,
  type User,
  type UserCredential,
  getAuth,
  getMultiFactorResolver,
  getRedirectResult,
  multiFactor,
  SAMLAuthProvider,
  signInWithEmailAndPassword,
  signInWithPopup,
  signInWithRedirect,
  TotpMultiFactorGenerator,
} from 'firebase/auth';

import { environment } from '../../environments/environment';
import type { FirebaseWebConfig } from '../../environments/environment.model';

/**
 * Tiempo maximo (ms) para las llamadas al Backend_FastAPI (R1.5, R1.6, R3.1):
 * fail-fast a 15 s con RxJS `timeout`.
 */
const BACKEND_TIMEOUT_MS = 15_000;

/** Error de dominio para condiciones de configuracion no resuelta (R9.7). */
export class ConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ConfigurationError';
  }
}

/**
 * Error de dominio generico de autenticacion. No revela que factor fallo
 * (R3.4, R3.5): el mensaje expuesto al usuario es intencionalmente generico.
 */
export class AuthenticationError extends Error {
  constructor(message = 'No fue posible completar la autenticacion.') {
    super(message);
    this.name = 'AuthenticationError';
  }
}

/** Tipo de ruta resuelto por el backend (R2.1, R2.2). */
export type RouteType = 'cliente' | 'interno';

/** Respuesta del endpoint `/api/v1/auth/route` (contrato del backend). */
export interface RouteResponse {
  readonly route_type: RouteType;
  readonly login_method: string;
  readonly correlation_id: string;
}

/** Claim enmascarado devuelto por el dashboard (R6.4). */
export interface MaskedClaim {
  readonly name: string;
  readonly value: string;
}

/** Respuesta del recurso protegido `/api/v1/dashboard` (R6.1, R6.6). */
export interface DashboardResponse {
  readonly display_name: string;
  readonly user_type: RouteType;
  readonly claims: readonly MaskedClaim[];
}

/**
 * Orquesta la autenticacion de la PoC: el Firebase Client SDK (autenticacion
 * directa del usuario) y las llamadas al Backend_FastAPI (enrutamiento, sesion,
 * dashboard, logout). Concentra la logica de orquestacion fuera de los
 * componentes (steering: separacion de capas; la UI no contiene logica de
 * negocio).
 *
 * Reglas transversales aplicadas:
 * - URL del backend y config de Firebase resueltas por entorno, sin defaults
 *   embebidos; si faltan, se bloquean las llamadas / la inicializacion y se
 *   expone un error de configuracion (R9.6, R9.7).
 * - `withCredentials: true` para que la cookie de sesion HttpOnly fluya; nunca
 *   se almacenan tokens en localStorage (la sesion vive en la cookie opaca).
 * - `timeout(15000)` en las llamadas al backend; ante timeout/error de
 *   enrutamiento, el identificador lo conserva el componente para reintentar
 *   (R1.5, R1.6).
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);

  /** Instancia perezosa del Firebase Client SDK (se crea al primer uso). */
  private firebaseApp: FirebaseApp | null = null;
  private firebaseAuth: Auth | null = null;

  /**
   * Resuelve la URL base del backend o lanza si no esta configurada (R9.7).
   * No usa ninguna direccion por defecto embebida.
   *
   * @returns la URL base del backend (incluye el prefijo `/api/v1`).
   */
  private requireApiBaseUrl(): string {
    const baseUrl = environment.apiBaseUrl.trim();
    if (baseUrl === '') {
      throw new ConfigurationError(
        'La URL del backend no esta configurada para este entorno.',
      );
    }
    return baseUrl.endsWith('/') ? baseUrl.slice(0, -1) : baseUrl;
  }

  /**
   * Resuelve la configuracion web publica de Firebase o lanza si no esta
   * configurada (R9.7). No usa valores por defecto embebidos.
   *
   * @returns la configuracion web publica de Firebase.
   */
  private requireFirebaseConfig(): FirebaseWebConfig {
    const config = environment.firebase;
    if (config === null) {
      throw new ConfigurationError(
        'La configuracion de Firebase no esta disponible para este entorno.',
      );
    }
    return config;
  }

  /**
   * Inicializa (una sola vez) el Firebase Client SDK con la configuracion
   * publica resuelta por entorno y devuelve la instancia de `Auth`.
   *
   * @returns la instancia de `Auth` del Client SDK.
   */
  private getFirebaseAuth(): Auth {
    if (this.firebaseAuth !== null) {
      return this.firebaseAuth;
    }
    const config = this.requireFirebaseConfig();
    this.firebaseApp = initializeApp({
      apiKey: config.apiKey,
      authDomain: config.authDomain,
      projectId: config.projectId,
      ...(config.appId !== undefined ? { appId: config.appId } : {}),
      ...(config.storageBucket !== undefined ? { storageBucket: config.storageBucket } : {}),
      ...(config.messagingSenderId !== undefined
        ? { messagingSenderId: config.messagingSenderId }
        : {}),
    });
    this.firebaseAuth = getAuth(this.firebaseApp);
    // Fijar el `tenantId` aisla la PoC en su tenant dedicado de Identity
    // Platform (separado del nivel de proyecto compartido). Al asignarlo a la
    // instancia de `Auth`, el inicio de sesion con correo/contrasena, la
    // inscripcion/resolucion de MFA (TOTP) y el `signInWithRedirect` (SAML)
    // operan todos dentro de ese tenant. El valor se resuelve por entorno
    // (sin hardcodear) y es opcional: si no esta configurado, la app opera a
    // nivel de proyecto.
    if (config.tenantId !== undefined && config.tenantId !== '') {
      this.firebaseAuth.tenantId = config.tenantId;
    }
    return this.firebaseAuth;
  }

  // ---------------------------------------------------------------------------
  // Llamadas al Backend_FastAPI (HttpClient + cookie de sesion + timeout 15 s)
  // ---------------------------------------------------------------------------

  /**
   * Solicita al backend la decision de enrutamiento para un identificador
   * (R2). La validacion de negocio ocurre en el servidor; el frontend solo
   * transmite el identificador normalizado.
   *
   * Ante timeout o error de enrutamiento (R1.6), el observable emite un error:
   * el componente conserva el identificador y permite reintentar.
   *
   * @param identifier identificador normalizado (ya recortado por el componente).
   * @returns observable con la ruta resuelta por el backend.
   */
  resolveRoute(identifier: string): Observable<RouteResponse> {
    let baseUrl: string;
    try {
      baseUrl = this.requireApiBaseUrl();
    } catch (error) {
      return throwError(() => error);
    }
    return this.http
      .post<RouteResponse>(
        `${baseUrl}/auth/route`,
        { identifier },
        { withCredentials: true },
      )
      .pipe(timeout(BACKEND_TIMEOUT_MS));
  }

  /**
   * Entrega el Firebase ID token al backend para que verifique el token y
   * establezca la sesion mediante una cookie `Secure`/`HttpOnly`/`SameSite`
   * (R5.6). La respuesta no devuelve tokens en el cuerpo; la cookie la gestiona
   * el navegador gracias a `withCredentials`.
   *
   * @param idToken Firebase ID token obtenido del Client SDK.
   * @returns observable que completa (204) cuando la sesion queda establecida.
   */
  establishSession(idToken: string): Observable<void> {
    let baseUrl: string;
    try {
      baseUrl = this.requireApiBaseUrl();
    } catch (error) {
      return throwError(() => error);
    }
    return this.http
      .post<void>(
        `${baseUrl}/auth/session`,
        { id_token: idToken },
        { withCredentials: true },
      )
      .pipe(timeout(BACKEND_TIMEOUT_MS));
  }

  /**
   * Consulta el recurso protegido. El backend es la fuente de verdad de la
   * sesion: valida la cookie y devuelve los claims enmascarados (R6).
   *
   * @returns observable con los datos del dashboard.
   */
  getDashboard(): Observable<DashboardResponse> {
    let baseUrl: string;
    try {
      baseUrl = this.requireApiBaseUrl();
    } catch (error) {
      return throwError(() => error);
    }
    return this.http
      .get<DashboardResponse>(`${baseUrl}/dashboard`, { withCredentials: true })
      .pipe(timeout(BACKEND_TIMEOUT_MS));
  }

  /**
   * Cierra la sesion en el backend (revoca tokens y borra la cookie). Es
   * idempotente del lado del servidor (R6.5).
   *
   * @returns observable que completa cuando la sesion queda invalidada.
   */
  logout(): Observable<void> {
    let baseUrl: string;
    try {
      baseUrl = this.requireApiBaseUrl();
    } catch (error) {
      return throwError(() => error);
    }
    return this.http
      .post<void>(`${baseUrl}/auth/logout`, {}, { withCredentials: true })
      .pipe(timeout(BACKEND_TIMEOUT_MS));
  }

  // ---------------------------------------------------------------------------
  // Orquestacion del Firebase Client SDK
  // ---------------------------------------------------------------------------

  /**
   * Flujo Cliente (R3): autentica con correo y contrasena. Si Firebase exige
   * segundo factor, el error `auth/multi-factor-auth-required` se propaga para
   * que el componente obtenga el resolver con `buildMfaResolver` y solicite el
   * TOTP. En exito directo (sin MFA) devuelve el ID token.
   *
   * @param email correo del Cliente.
   * @param password contrasena del Cliente.
   * @returns observable con el `UserCredential` resultante.
   */
  signInCliente(email: string, password: string): Observable<UserCredential> {
    let auth: Auth;
    try {
      auth = this.getFirebaseAuth();
    } catch (error) {
      return throwError(() => error);
    }
    return from(signInWithEmailAndPassword(auth, email, password));
  }

  /**
   * Obtiene el `MultiFactorResolver` a partir del error de MFA requerido
   * capturado en `signInCliente` (R3.2). El componente usa el resolver para
   * conocer los factores inscritos y luego completar con `resolveTotpSignIn`.
   *
   * @param error error lanzado por el Client SDK (`auth/multi-factor-auth-required`).
   * @returns el resolver de MFA asociado al intento de inicio de sesion.
   */
  buildMfaResolver(error: unknown): MultiFactorResolver {
    const auth = this.getFirebaseAuth();
    return getMultiFactorResolver(auth, error as Parameters<typeof getMultiFactorResolver>[1]);
  }

  /**
   * Completa el inicio de sesion con TOTP como segundo factor (R3.2, R3.3)
   * usando `TotpMultiFactorGenerator.assertionForSignIn`.
   *
   * @param resolver resolver obtenido de `buildMfaResolver`.
   * @param enrollmentId id del factor TOTP inscrito (del `resolver.hints`).
   * @param oneTimePassword codigo TOTP de 6 digitos ingresado por el usuario.
   * @returns observable con el `UserCredential` tras completar el 2FA.
   */
  resolveTotpSignIn(
    resolver: MultiFactorResolver,
    enrollmentId: string,
    oneTimePassword: string,
  ): Observable<UserCredential> {
    const assertion = TotpMultiFactorGenerator.assertionForSignIn(
      enrollmentId,
      oneTimePassword,
    );
    return from(resolver.resolveSignIn(assertion));
  }

  /**
   * Determina si un error del Client SDK corresponde a "segundo factor
   * requerido" (`auth/multi-factor-auth-required`), es decir, un Cliente ya
   * inscrito en TOTP cuyo inicio de sesion debe completarse con el codigo
   * (R3.2). El componente usa este helper para distinguir ese caso del de un
   * Cliente aun no inscrito (que debe pasar por el enrolamiento TOTP).
   *
   * @param error error capturado al invocar `signInCliente`.
   * @returns `true` si el error indica que se requiere el segundo factor.
   */
  isMfaRequiredError(error: unknown): boolean {
    return (error as { code?: string } | null)?.code ===
      'auth/multi-factor-auth-required';
  }

  /**
   * Inicia el enrolamiento TOTP de un Cliente autenticado sin segundo factor
   * (primera vez): obtiene la sesion de MFA del usuario y genera el secreto
   * TOTP con `TotpMultiFactorGenerator.generateSecret` (R3). El `TotpSecret`
   * resultante se usa para construir el QR / clave de configuracion y, tras la
   * verificacion del codigo, para completar el enrolamiento.
   *
   * @param user usuario autenticado del Client SDK (sin TOTP inscrito aun).
   * @returns observable con el `TotpSecret` generado para el enrolamiento.
   */
  startTotpEnrollment(user: User): Observable<TotpSecret> {
    const multiFactorUser: MultiFactorUser = multiFactor(user);
    return from(
      multiFactorUser
        .getSession()
        .then((session) =>
          TotpMultiFactorGenerator.generateSecret(session),
        ),
    );
  }

  /**
   * Construye la URL `otpauth://` del secreto TOTP para que el usuario la
   * escanee (o la ingrese manualmente) en su aplicacion de autenticacion.
   * Delega en `TotpSecret.generateQrCodeUrl`.
   *
   * @param secret secreto TOTP obtenido de `startTotpEnrollment`.
   * @param accountName nombre de cuenta mostrado en la app (el correo del usuario).
   * @returns la URL `otpauth://` lista para generar el QR / clave de configuracion.
   */
  buildTotpQrCodeUrl(secret: TotpSecret, accountName: string): string {
    return secret.generateQrCodeUrl(accountName, 'PoC Auth 2FA');
  }

  /**
   * Completa el enrolamiento TOTP del Cliente (primera vez): construye la
   * asercion de enrolamiento con el codigo de 6 digitos ingresado por el
   * usuario (`TotpMultiFactorGenerator.assertionForEnrollment`) e inscribe el
   * factor con `multiFactor(user).enroll` (R3). Tras completar, el usuario
   * queda autenticado y con TOTP inscrito.
   *
   * @param user usuario autenticado del Client SDK a inscribir.
   * @param secret secreto TOTP generado en `startTotpEnrollment`.
   * @param code codigo TOTP de 6 digitos generado por la app de autenticacion.
   * @param displayName nombre descriptivo del factor inscrito.
   * @returns observable que completa cuando el factor TOTP queda inscrito.
   */
  completeTotpEnrollment(
    user: User,
    secret: TotpSecret,
    code: string,
    displayName = 'TOTP',
  ): Observable<void> {
    const assertion = TotpMultiFactorGenerator.assertionForEnrollment(
      secret,
      code,
    );
    return from(multiFactor(user).enroll(assertion, displayName));
  }

  /**
   * Flujo Interno (R4): inicia la autenticacion SAML hacia F5 APM a traves de
   * Firebase como Service Provider usando una ventana emergente
   * (`signInWithPopup`). A diferencia de `signInWithRedirect`, el popup
   * completa el flujo SAML en una ventana aparte y devuelve el `UserCredential`
   * directamente a esta instancia de `Auth`, sin depender del estado de
   * redirect persistido entre dominios (que los navegadores pierden al bloquear
   * cookies de terceros cuando el `authDomain` difiere del origen de la app).
   * El `samlProviderId` se resuelve por entorno.
   *
   * @returns observable con el `UserCredential` resultante del popup SAML.
   */
  signInInterno(): Observable<UserCredential> {
    let auth: Auth;
    let samlProviderId: string;
    try {
      auth = this.getFirebaseAuth();
      samlProviderId = this.requireFirebaseConfig().samlProviderId;
    } catch (error) {
      return throwError(() => error);
    }
    const provider = new SAMLAuthProvider(samlProviderId);
    return from(signInWithPopup(auth, provider));
  }

  /**
   * Maneja el retorno del redirect SAML del Interno (R4.6): recupera el
   * resultado con `getRedirectResult`. Si no hay redirect pendiente, Firebase
   * devuelve `null`.
   *
   * @returns observable con el `UserCredential` del redirect, o `null`.
   */
  completeRedirectSignIn(): Observable<UserCredential | null> {
    let auth: Auth;
    try {
      auth = this.getFirebaseAuth();
    } catch (error) {
      return throwError(() => error);
    }
    return from(getRedirectResult(auth));
  }

  /**
   * Obtiene el Firebase ID token del usuario autenticado para entregarlo al
   * backend en `establishSession`.
   *
   * @param user usuario autenticado del Client SDK.
   * @param forceRefresh fuerza la renovacion del token si es `true`.
   * @returns observable con el ID token (JWT) firmado por Firebase.
   */
  getIdToken(user: User, forceRefresh = false): Observable<string> {
    return from(user.getIdToken(forceRefresh));
  }
}
