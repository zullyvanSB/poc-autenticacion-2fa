import { Component, type OnInit, inject, signal } from '@angular/core';
import {
  AbstractControl,
  FormControl,
  ReactiveFormsModule,
  ValidationErrors,
  Validators,
} from '@angular/forms';
import { Router } from '@angular/router';
import { DomSanitizer, type SafeHtml } from '@angular/platform-browser';

import { buildTotpQrSvg } from '../core/qr-code';

import type {
  MultiFactorResolver,
  TotpSecret,
  User,
  UserCredential,
} from 'firebase/auth';

import {
  AuthService,
  ConfigurationError,
  type RouteResponse,
} from '../core/auth.service';

/** Longitud maxima permitida del identificador en el cliente (R1.4). */
const IDENTIFIER_MAX_LENGTH = 254;

/** Longitud exacta del codigo TOTP de 6 digitos numericos (R3). */
const TOTP_LENGTH = 6;

/** Patron de codigo TOTP: exactamente 6 digitos numericos (R3). */
const TOTP_PATTERN = /^\d{6}$/;

/** Mensaje generico de error de ingreso (R1.6): no revela el motivo tecnico. */
const GENERIC_LOGIN_ERROR =
  'No fue posible procesar el ingreso. Intenta nuevamente.';

/** Mensaje de error de configuracion (R9.7). */
const CONFIG_LOGIN_ERROR =
  'El ingreso no esta disponible por un problema de configuracion.';

/**
 * Mensaje GENERICO de error de autenticacion (R3.4, R3.5): toda condicion de
 * fallo (contrasena, codigo TOTP invalido/expirado, sesion) produce el mismo
 * mensaje y nunca revela que factor fallo.
 */
const GENERIC_AUTH_ERROR =
  'No fue posible completar la autenticacion. Verifica tus datos e intentalo de nuevo.';

/**
 * Fases del flujo de ingreso unificado del Cliente, modeladas como una maquina
 * de estados interna del componente (signals), sin cruzar rutas:
 *
 * - `identifier`   : unico campo de identificador (comportamiento base, R1.1).
 * - `password`     : tras ruta `cliente`, se solicita la contrasena.
 * - `enroll-totp`  : el inicio de sesion funciono SIN MFA (Cliente nuevo aun no
 *                    inscrito): se muestra el QR/clave para inscribir TOTP.
 * - `verify-totp`  : el inicio de sesion exigio MFA (Cliente ya inscrito): se
 *                    solicita el codigo TOTP para verificar.
 *
 * Mantener todo el flujo del Cliente dentro de este componente evita pasar
 * objetos no serializables del Firebase Client SDK (resolver/secret) por el
 * estado del router. El flujo Interno conserva su comportamiento: redirect SAML.
 */
type LoginPhase = 'identifier' | 'password' | 'enroll-totp' | 'verify-totp';

/**
 * Pantalla de ingreso unificada identifier-first (ruta `login`, R1.1) que
 * orquesta el flujo COMPLETO del Cliente mediante una maquina de estados
 * interna (signals), sin navegar entre rutas para no transportar objetos
 * transitorios del Client SDK (resolver/secret) por el estado del router.
 *
 * Fase `identifier` (R1.1-R1.4, R1.6): un unico campo editable, validado en el
 * cliente (no vacio tras recortar, longitud maxima). Al continuar, consulta al
 * backend la decision de ruta via `AuthService.resolveRoute` (R1.2):
 * - ruta `interno`: dispara el redirect SAML con `signInInterno` (R4), sin
 *   romper el flujo existente.
 * - ruta `cliente`: avanza a la fase `password`.
 *
 * Fase `password` (R3): solicita la contrasena y llama a `signInCliente`:
 * - exito SIN MFA (Cliente nuevo): genera el secreto TOTP (`startTotpEnrollment`)
 *   y la URL `otpauth://` (`buildTotpQrCodeUrl`) y pasa a `enroll-totp`.
 * - error de MFA requerido (`isMfaRequiredError`): construye el resolver
 *   (`buildMfaResolver`), lo guarda en un campo del componente (no en el router)
 *   y pasa a `verify-totp`.
 *
 * Fase `enroll-totp` (R3): muestra la clave de configuracion y la URL otpauth,
 * recibe el codigo de 6 digitos y completa la inscripcion con
 * `completeTotpEnrollment`; luego obtiene el ID token y establece la sesion
 * server-side (cookie HttpOnly) antes de navegar al dashboard.
 *
 * Fase `verify-totp` (R3): recibe el codigo de 6 digitos y completa el inicio
 * de sesion con `resolveTotpSignIn`; luego obtiene el ID token, establece la
 * sesion y navega al dashboard.
 *
 * Separacion de capas (steering): la UI solo orquesta; toda la logica del
 * Client SDK (sign-in, generacion/aserciones TOTP, resolver) vive en
 * `AuthService`. Mensajes de error genericos (R3.4, R3.5). Ningun token se
 * guarda en localStorage: la sesion vive en la cookie opaca del backend.
 */
@Component({
  selector: 'app-login',
  standalone: true,
  imports: [ReactiveFormsModule],
  template: `
    <section class="login" aria-label="Ingreso">
      <h1 class="login__title">Ingreso</h1>

      <!-- Retorno del redirect SAML (Interno): indicador mientras se completa -->
      @if (processingRedirect()) {
        <p class="login__hint" role="status" aria-live="polite">
          Completando el inicio de sesion...
        </p>
      }

      <!-- Fase identificador (R1.1) -->
      @if (phase() === 'identifier') {
        <form class="login__form" novalidate>
          <label class="login__label" for="identifier">Identificador</label>
          <input
            id="identifier"
            type="text"
            class="login__input"
            autocomplete="username"
            autocapitalize="none"
            spellcheck="false"
            [formControl]="identifier"
            [attr.maxlength]="maxLength"
            [attr.aria-invalid]="showIdentifierError() ? 'true' : null"
            [attr.aria-describedby]="identifierDescribedBy()"
          />

          @if (showIdentifierError()) {
            <p id="identifier-error" class="login__message login__message--error" role="alert">
              {{ identifierErrorMessage() }}
            </p>
          }

          @if (backendError()) {
            <p id="login-error" class="login__message login__message--error" role="alert">
              {{ backendError() }}
            </p>
          }

          <button
            type="button"
            class="login__submit"
            (click)="onSubmitIdentifier()"
            [disabled]="submitting()"
          >
            @if (submitting()) {
              Procesando...
            } @else {
              Continuar
            }
          </button>
        </form>
      }

      <!-- Fase contrasena (R3) -->
      @if (phase() === 'password') {
        <form class="login__form" novalidate>
          <p class="login__hint">
            Ingresando como <strong>{{ identifierValue() }}</strong>
          </p>
          <label class="login__label" for="password">Contrasena</label>
          <input
            id="password"
            type="password"
            class="login__input"
            autocomplete="current-password"
            [formControl]="password"
            [attr.aria-invalid]="showPasswordError() ? 'true' : null"
            [attr.aria-describedby]="authError() ? 'auth-error' : null"
          />

          @if (showPasswordError()) {
            <p id="password-error" class="login__message login__message--error" role="alert">
              La contrasena es obligatoria.
            </p>
          }

          @if (authError()) {
            <p id="auth-error" class="login__message login__message--error" role="alert" aria-live="assertive">
              {{ authError() }}
            </p>
          }

          <button
            type="button"
            class="login__submit"
            (click)="onSubmitPassword()"
            [disabled]="submitting()"
          >
            @if (submitting()) {
              Procesando...
            } @else {
              Continuar
            }
          </button>
        </form>
      }

      <!-- Fase enrolamiento TOTP de primera vez (R3) -->
      @if (phase() === 'enroll-totp') {
        <div class="login__form">
          <h2 class="login__subtitle">Configura la verificacion en dos pasos</h2>
          <p class="login__hint">
            Abre tu aplicacion de autenticacion (por ejemplo, Google
            Authenticator), elige <strong>"Ingresar una clave de configuracion"</strong>
            y usa la clave de abajo. Luego escribe el codigo de 6 digitos para
            confirmar.
          </p>

          <!--
            NOTA (PoC): el navegador no puede renderizar una URL otpauth:// como
            imagen sin una libreria de QR, y no agregamos dependencias nuevas ni
            llamadas externas (p. ej. generadores de QR por URL). Por eso
            mostramos la clave de configuracion (secretKey) de forma prominente
            para ingreso manual, y la URL otpauth:// como texto para quien pueda
            generar el QR por su cuenta.
          -->
          @if (totpQrSvg() !== null) {
            <div class="login__qr" [innerHTML]="totpQrSvg()"></div>
            <p class="login__hint">
              Escanea el codigo QR con tu aplicacion de autenticacion. Si no
              puedes escanearlo, ingresa manualmente la clave de abajo.
            </p>
          }

          <label class="login__label" for="totp-secret">Clave de configuracion</label>
          <output id="totp-secret" class="login__secret">{{ totpSecretKey() }}</output>

          <details class="login__details">
            <summary>Ver URL otpauth</summary>
            <output class="login__otpauth">{{ totpQrUrl() }}</output>
          </details>

          <label class="login__label" for="enroll-code">
            Codigo de 6 digitos de tu aplicacion
          </label>
          <input
            id="enroll-code"
            type="text"
            class="login__input"
            inputmode="numeric"
            autocomplete="one-time-code"
            [maxlength]="totpLength"
            [formControl]="totpCode"
            [attr.aria-invalid]="showTotpError() ? 'true' : null"
            aria-describedby="enroll-help enroll-error"
          />
          <p id="enroll-help" class="login__message">
            Codigo numerico de {{ totpLength }} digitos.
          </p>

          @if (authError()) {
            <p id="enroll-error" class="login__message login__message--error" role="alert" aria-live="assertive">
              {{ authError() }}
            </p>
          }

          <button
            type="button"
            class="login__submit"
            (click)="onConfirmEnrollment()"
            [disabled]="submitting()"
          >
            @if (submitting()) {
              Procesando...
            } @else {
              Confirmar
            }
          </button>
        </div>
      }

      <!-- Fase verificacion TOTP (Cliente ya inscrito, R3) -->
      @if (phase() === 'verify-totp') {
        <form class="login__form" novalidate>
          <h2 class="login__subtitle">Verificacion en dos pasos</h2>
          <label class="login__label" for="verify-code">
            Ingresa el codigo de 6 digitos de tu aplicacion de autenticacion
          </label>
          <input
            id="verify-code"
            type="text"
            class="login__input"
            inputmode="numeric"
            autocomplete="one-time-code"
            [maxlength]="totpLength"
            [formControl]="totpCode"
            [attr.aria-invalid]="showTotpError() ? 'true' : null"
            aria-describedby="verify-help verify-error"
          />
          <p id="verify-help" class="login__message">
            Codigo numerico de {{ totpLength }} digitos.
          </p>

          @if (authError()) {
            <p id="verify-error" class="login__message login__message--error" role="alert" aria-live="assertive">
              {{ authError() }}
            </p>
          }

          <button
            type="button"
            class="login__submit"
            (click)="onConfirmVerification()"
            [disabled]="submitting()"
          >
            @if (submitting()) {
              Procesando...
            } @else {
              Verificar
            }
          </button>
        </form>
      }
    </section>
  `,
  styles: [
    `
      .login {
        max-width: 24rem;
        margin: 4rem auto;
        padding: 0 1rem;
        display: flex;
        flex-direction: column;
        gap: 1rem;
      }
      .login__title {
        margin: 0;
        font-size: 1.5rem;
      }
      .login__subtitle {
        margin: 0;
        font-size: 1.15rem;
      }
      .login__form {
        display: flex;
        flex-direction: column;
        gap: 0.5rem;
      }
      .login__label {
        font-weight: 600;
      }
      .login__hint {
        margin: 0;
        font-size: 0.9rem;
      }
      .login__input {
        padding: 0.6rem 0.75rem;
        font-size: 1rem;
        border: 1px solid currentColor;
        border-radius: 0.375rem;
      }
      .login__qr {
        align-self: center;
        line-height: 0;
      }
      .login__secret {
        display: block;
        padding: 0.6rem 0.75rem;
        font-family: monospace;
        font-size: 1.1rem;
        letter-spacing: 0.1em;
        word-break: break-all;
        border: 1px dashed currentColor;
        border-radius: 0.375rem;
      }
      .login__details {
        font-size: 0.85rem;
      }
      .login__otpauth {
        display: block;
        margin-top: 0.5rem;
        font-family: monospace;
        word-break: break-all;
      }
      .login__message {
        margin: 0;
        font-size: 0.875rem;
      }
      .login__message--error {
        color: #b00020;
      }
      .login__submit {
        margin-top: 0.5rem;
        padding: 0.6rem 1rem;
        font-size: 1rem;
        cursor: pointer;
        border-radius: 0.375rem;
      }
      .login__submit:disabled {
        cursor: progress;
        opacity: 0.7;
      }
    `,
  ],
})
export class LoginComponent implements OnInit {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly sanitizer = inject(DomSanitizer);

  /** Longitud maxima expuesta al template (R1.4). */
  protected readonly maxLength = IDENTIFIER_MAX_LENGTH;

  /** Longitud del codigo TOTP expuesta al template. */
  protected readonly totpLength = TOTP_LENGTH;

  /** Fase actual de la maquina de estados del flujo de ingreso. */
  protected readonly phase = signal<LoginPhase>('identifier');

  /**
   * Unico control del formulario de identificador (R1.1). Valida no vacio tras
   * recortar espacios (R1.3) y longitud maxima (R1.4) antes de permitir envio.
   */
  protected readonly identifier = new FormControl('', {
    nonNullable: true,
    validators: [
      Validators.required,
      trimmedNotEmptyValidator,
      Validators.maxLength(IDENTIFIER_MAX_LENGTH),
    ],
  });

  /** Control de contrasena del flujo Cliente (fase `password`). */
  protected readonly password = new FormControl('', {
    nonNullable: true,
    validators: [Validators.required],
  });

  /** Control del codigo TOTP (fases `enroll-totp` y `verify-totp`). */
  protected readonly totpCode = new FormControl('', {
    nonNullable: true,
    validators: [
      Validators.required,
      Validators.minLength(TOTP_LENGTH),
      Validators.maxLength(TOTP_LENGTH),
      Validators.pattern(TOTP_PATTERN),
    ],
  });

  /** Indica si hay una operacion en curso (deshabilita botones, evita reenvios). */
  protected readonly submitting = signal(false);

  /**
   * `true` mientras se resuelve el retorno del redirect SAML del Interno
   * (R4.6). Al volver de F5 APM, el navegador aterriza en esta pantalla; se
   * muestra un indicador y se completa la sesion antes de ir al dashboard.
   */
  protected readonly processingRedirect = signal(false);

  /** Mensaje de error generico del backend en la fase identificador (R1.6). */
  protected readonly backendError = signal<string | null>(null);

  /** Mensaje de error generico de autenticacion (R3.4, R3.5). */
  protected readonly authError = signal<string | null>(null);

  /** `true` cuando ya se intento enviar el identificador (controla el error). */
  private readonly identifierSubmitAttempted = signal(false);

  /** `true` cuando ya se intento enviar la contrasena. */
  private readonly passwordSubmitAttempted = signal(false);

  /** `true` cuando ya se intento enviar el codigo TOTP. */
  private readonly totpSubmitAttempted = signal(false);

  /** Correo normalizado del Cliente (identificador recortado) en el flujo. */
  protected readonly identifierValue = signal('');

  /**
   * Secreto TOTP generado para el enrolamiento de primera vez. Es un objeto
   * transitorio del Client SDK; se guarda en el componente (NO en el router).
   */
  private totpSecret: TotpSecret | null = null;

  /** URL `otpauth://` derivada del secreto, mostrada en la fase `enroll-totp`. */
  protected readonly totpQrUrl = signal('');

  /** Clave de configuracion (secretKey) para ingreso manual en la app TOTP. */
  protected readonly totpSecretKey = signal('');

  /**
   * SVG del codigo QR de enrolamiento, generado localmente a partir de la URL
   * otpauth:// y sanitizado para insertarse con [innerHTML]. `null` si aun no
   * se ha generado o si la generacion fallo (en ese caso se usa la clave manual).
   */
  protected readonly totpQrSvg = signal<SafeHtml | null>(null);

  /**
   * Usuario autenticado (sin MFA) pendiente de enrolar TOTP. Objeto transitorio
   * del Client SDK; se conserva en el componente, no en el estado del router.
   */
  private pendingUser: User | null = null;

  /**
   * Resolver de MFA para un Cliente ya inscrito. Objeto transitorio del Client
   * SDK; se conserva en el componente, no en el estado del router.
   */
  private resolver: MultiFactorResolver | null = null;

  /**
   * Al inicializar, intenta consumir un resultado de redirect SAML pendiente
   * (R4.6). `signInWithRedirect` devuelve al usuario a ESTA pantalla (la que
   * inicio el redirect), por lo que es aqui donde debe recuperarse el
   * resultado con `getRedirectResult` (via `AuthService.completeRedirectSignIn`).
   * Si hay un usuario autenticado por SAML, se obtiene el ID token y se
   * establece la sesion server-side antes de navegar al dashboard. Si no hay
   * redirect pendiente (`null`), es un ingreso normal y no se hace nada. Ante
   * fallo, se muestra un mensaje generico y se permanece en la pantalla (R4.7).
   */
  ngOnInit(): void {
    // El chequeo del resultado del redirect corre en segundo plano SIN
    // bloquear ni ocultar el formulario de ingreso: `getRedirectResult` puede
    // tardar o no resolver cuando no hay redirect pendiente, y la pantalla de
    // ingreso debe seguir usable mientras tanto. Solo si vuelve un usuario
    // autenticado por SAML se muestra el indicador y se completa la sesion.
    this.auth.completeRedirectSignIn().subscribe({
      next: (credential) => {
        if (credential === null) {
          // Ingreso normal (sin redirect pendiente): no se hace nada.
          return;
        }
        // Retorno de F5 APM con usuario autenticado: completar la sesion.
        this.processingRedirect.set(true);
        this.establishSessionAndContinue(credential.user);
      },
      error: () => {
        // Sin redirect valido o fallo al recuperarlo: se permanece en la
        // pantalla de ingreso, que ya es usable. No se muestra error para no
        // confundir en un ingreso normal (el usuario no inicio ningun SAML).
        this.processingRedirect.set(false);
      },
    });
  }

  /** `true` si el control de identificador es invalido tras interaccion/intento. */
  protected showIdentifierError(): boolean {
    return (
      this.identifier.invalid &&
      (this.identifier.touched || this.identifierSubmitAttempted())
    );
  }

  /** `true` si el control de contrasena es invalido tras interaccion/intento. */
  protected showPasswordError(): boolean {
    return (
      this.password.invalid &&
      (this.password.touched || this.passwordSubmitAttempted())
    );
  }

  /** `true` si el control de codigo TOTP es invalido tras interaccion/intento. */
  protected showTotpError(): boolean {
    return (
      this.totpCode.invalid &&
      (this.totpCode.touched || this.totpSubmitAttempted())
    );
  }

  /** Mensaje de validacion del identificador segun el error presente. */
  protected identifierErrorMessage(): string {
    const errors = this.identifier.errors;
    if (errors === null) {
      return '';
    }
    if (errors['maxlength'] !== undefined) {
      return `El identificador supera la longitud maxima de ${IDENTIFIER_MAX_LENGTH} caracteres.`;
    }
    return 'El identificador es obligatorio.';
  }

  /** Ids que describen el campo identificador para `aria-describedby`. */
  protected identifierDescribedBy(): string | null {
    const ids: string[] = [];
    if (this.showIdentifierError()) {
      ids.push('identifier-error');
    }
    if (this.backendError() !== null) {
      ids.push('login-error');
    }
    return ids.length > 0 ? ids.join(' ') : null;
  }

  /**
   * Fase identificador (R1.2): valida en el cliente y consulta la ruta al
   * backend. Ruta `interno` -> redirect SAML; ruta `cliente` -> fase password.
   * Conserva el identificador ante error (R1.6).
   */
  protected onSubmitIdentifier(): void {
    this.identifierSubmitAttempted.set(true);
    this.backendError.set(null);

    if (this.identifier.invalid) {
      this.identifier.markAsTouched();
      return;
    }
    if (this.submitting()) {
      return;
    }

    const normalizedIdentifier = this.identifier.value.trim();
    this.submitting.set(true);

    this.auth.resolveRoute(normalizedIdentifier).subscribe({
      next: (route: RouteResponse) => {
        if (route.route_type === 'interno') {
          this.startInternoRedirect();
          return;
        }
        // Ruta Cliente: continuar al paso de contrasena dentro del mismo
        // componente (sin navegar), conservando el identificador normalizado.
        this.identifierValue.set(normalizedIdentifier);
        this.submitting.set(false);
        this.phase.set('password');
      },
      error: (error: unknown) => {
        this.submitting.set(false);
        this.backendError.set(
          error instanceof ConfigurationError
            ? CONFIG_LOGIN_ERROR
            : GENERIC_LOGIN_ERROR,
        );
      },
    });
  }

  /**
   * Fase contrasena (R3): intenta `signInCliente`. En exito SIN MFA inicia el
   * enrolamiento TOTP; si Firebase exige MFA (Cliente ya inscrito) construye el
   * resolver y pasa a verificacion; cualquier otro error es generico (R3.4).
   */
  protected onSubmitPassword(): void {
    this.passwordSubmitAttempted.set(true);
    this.authError.set(null);

    if (this.password.invalid) {
      this.password.markAsTouched();
      return;
    }
    if (this.submitting()) {
      return;
    }

    this.submitting.set(true);
    const email = this.identifierValue();

    this.auth.signInCliente(email, this.password.value).subscribe({
      next: (credential: UserCredential) => this.beginTotpEnrollment(credential),
      error: (error: unknown) => {
        if (this.auth.isMfaRequiredError(error)) {
          this.beginTotpVerification(error);
          return;
        }
        this.submitting.set(false);
        this.authError.set(GENERIC_AUTH_ERROR);
      },
    });
  }

  /**
   * Fase enrolamiento (R3): confirma el enrolamiento TOTP con el codigo
   * ingresado; en exito obtiene el ID token y establece la sesion server-side
   * antes de ir al dashboard. Ante fallo, mensaje generico y reintento.
   */
  protected onConfirmEnrollment(): void {
    this.totpSubmitAttempted.set(true);
    this.authError.set(null);

    const user = this.pendingUser;
    const secret = this.totpSecret;
    if (user === null || secret === null) {
      this.authError.set(GENERIC_AUTH_ERROR);
      return;
    }
    if (this.totpCode.invalid) {
      this.totpCode.markAsTouched();
      return;
    }
    if (this.submitting()) {
      return;
    }

    this.submitting.set(true);
    this.auth
      .completeTotpEnrollment(user, secret, this.totpCode.value)
      .subscribe({
        next: () => this.establishSessionAndContinue(user),
        error: () => this.failTotpRetry(),
      });
  }

  /**
   * Fase verificacion (R3): completa el inicio de sesion del Cliente ya
   * inscrito con el codigo TOTP; en exito obtiene el ID token y establece la
   * sesion antes de ir al dashboard. Ante fallo, mensaje generico y reintento.
   */
  protected onConfirmVerification(): void {
    this.totpSubmitAttempted.set(true);
    this.authError.set(null);

    const resolver = this.resolver;
    if (resolver === null) {
      this.authError.set(GENERIC_AUTH_ERROR);
      return;
    }
    if (this.totpCode.invalid) {
      this.totpCode.markAsTouched();
      return;
    }
    if (this.submitting()) {
      return;
    }

    const [hint] = resolver.hints;
    const enrollmentId = hint?.uid ?? null;
    if (enrollmentId === null) {
      this.submitting.set(false);
      this.authError.set(GENERIC_AUTH_ERROR);
      return;
    }

    this.submitting.set(true);
    this.auth
      .resolveTotpSignIn(resolver, enrollmentId, this.totpCode.value)
      .subscribe({
        next: (credential: UserCredential) =>
          this.establishSessionAndContinue(credential.user),
        error: () => this.failTotpRetry(),
      });
  }

  /**
   * Dispara la autenticacion SAML del Interno (R4) mediante ventana emergente
   * (`signInWithPopup`). A diferencia del redirect, el popup completa el flujo
   * SAML y devuelve el `UserCredential` directamente aqui, por lo que se obtiene
   * el ID token y se establece la sesion server-side en el mismo flujo (sin
   * depender del retorno por `getRedirectResult`, que los navegadores pierden
   * entre dominios al bloquear cookies de terceros). Ante fallo (popup cerrado,
   * bloqueado o asercion rechazada) se muestra un mensaje generico.
   */
  private startInternoRedirect(): void {
    this.auth.signInInterno().subscribe({
      next: (credential: UserCredential) => {
        this.processingRedirect.set(true);
        this.establishSessionAndContinue(credential.user);
      },
      error: () => {
        this.submitting.set(false);
        this.backendError.set(GENERIC_LOGIN_ERROR);
      },
    });
  }

  /**
   * Prepara la fase de enrolamiento TOTP para un Cliente nuevo (sin MFA):
   * genera el secreto, deriva la URL otpauth y la clave de configuracion, y
   * cambia a la fase `enroll-totp`. Ante fallo al generar el secreto, mensaje
   * generico en la fase de contrasena.
   *
   * @param credential credenciales del Cliente autenticado sin segundo factor.
   */
  private beginTotpEnrollment(credential: UserCredential): void {
    const user = credential.user;
    this.auth.startTotpEnrollment(user).subscribe({
      next: (secret: TotpSecret) => {
        this.pendingUser = user;
        this.totpSecret = secret;
        this.totpSecretKey.set(secret.secretKey);
        const otpauthUrl = this.auth.buildTotpQrCodeUrl(
          secret,
          this.identifierValue(),
        );
        this.totpQrUrl.set(otpauthUrl);
        // QR generado localmente (sin red, sin fuga del secreto). Si por
        // cualquier causa falla la codificacion, se degrada a la clave manual.
        try {
          this.totpQrSvg.set(
            this.sanitizer.bypassSecurityTrustHtml(buildTotpQrSvg(otpauthUrl)),
          );
        } catch {
          this.totpQrSvg.set(null);
        }
        this.resetTotpField();
        this.submitting.set(false);
        this.phase.set('enroll-totp');
      },
      error: () => {
        this.submitting.set(false);
        this.authError.set(GENERIC_AUTH_ERROR);
      },
    });
  }

  /**
   * Prepara la fase de verificacion TOTP para un Cliente ya inscrito: construye
   * el resolver a partir del error de MFA requerido y cambia a `verify-totp`.
   *
   * @param error error `auth/multi-factor-auth-required` capturado en el sign-in.
   */
  private beginTotpVerification(error: unknown): void {
    try {
      this.resolver = this.auth.buildMfaResolver(error);
    } catch {
      this.submitting.set(false);
      this.authError.set(GENERIC_AUTH_ERROR);
      return;
    }
    this.resetTotpField();
    this.submitting.set(false);
    this.phase.set('verify-totp');
  }

  /**
   * Obtiene el Firebase ID token del usuario autenticado y establece la sesion
   * de backend (cookie HttpOnly) antes de navegar al dashboard. Comun al final
   * de enrolamiento y verificacion. Ante cualquier fallo, mensaje generico.
   *
   * @param user usuario autenticado (y ya con TOTP inscrito/verificado).
   */
  private establishSessionAndContinue(user: User): void {
    this.auth.getIdToken(user, true).subscribe({
      next: (idToken: string) => {
        this.auth.establishSession(idToken).subscribe({
          next: () => {
            this.submitting.set(false);
            void this.router.navigate(['/dashboard']);
          },
          error: () => {
            this.submitting.set(false);
            this.authError.set(GENERIC_AUTH_ERROR);
          },
        });
      },
      error: () => {
        this.submitting.set(false);
        this.authError.set(GENERIC_AUTH_ERROR);
      },
    });
  }

  /** Centraliza el fallo de codigo TOTP: mensaje generico y permitir reintento. */
  private failTotpRetry(): void {
    this.submitting.set(false);
    this.authError.set(GENERIC_AUTH_ERROR);
  }

  /** Limpia el campo de codigo TOTP y su estado de intento entre fases. */
  private resetTotpField(): void {
    this.totpCode.reset('');
    this.totpSubmitAttempted.set(false);
  }
}

/**
 * Validador: el identificador no puede quedar vacio tras recortar los espacios
 * iniciales y finales (R1.3).
 *
 * @param control control del formulario a validar.
 * @returns `{ required: true }` si el valor recortado queda vacio; `null` si es valido.
 */
function trimmedNotEmptyValidator(
  control: AbstractControl<string>,
): ValidationErrors | null {
  return (control.value ?? '').trim() === '' ? { required: true } : null;
}
