import { ChangeDetectionStrategy, Component, type OnInit, inject, signal } from '@angular/core';
import { FormControl, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';

import type { MultiFactorResolver, User, UserCredential } from 'firebase/auth';

import { AuthService } from '../core/auth.service';

/**
 * Datos que el `LoginComponent` entrega a este componente mediante el estado de
 * navegacion del router (`router.navigate(['/2fa'], { state })`) cuando Firebase
 * exige segundo factor para un Cliente (R3.2).
 *
 * El `MultiFactorResolver` es un objeto transitorio del Firebase Client SDK y no
 * puede serializarse en parametros de ruta; viaja por el estado de navegacion en
 * memoria. Si no esta presente (p. ej. recarga de la pagina o acceso directo a
 * `/2fa`), el componente trata el caso como retorno del redirect de Interno (R4).
 */
interface TwoFactorNavigationState {
  readonly resolver?: MultiFactorResolver;
}

/** Longitud exacta del codigo TOTP de 6 digitos numericos (R3.2, R4.3). */
const TOTP_LENGTH = 6;

/** Patron de codigo TOTP: exactamente 6 digitos numericos (R3.2, R4.3). */
const TOTP_PATTERN = /^\d{6}$/;

/**
 * Mensaje de error GENERICO mostrado al usuario. No revela que factor fallo ni
 * el motivo especifico (R3.4, R3.5, R4.7, R4.9): toda condicion de fallo de
 * autenticacion (credenciales, TOTP invalido/expirado, SAML, bloqueo) produce
 * el mismo mensaje.
 */
const GENERIC_AUTH_ERROR = 'No fue posible completar la autenticacion. Verifica el codigo e intentalo de nuevo.';

/** Mensaje generico cuando no hay un flujo de 2FA valido en curso. */
const NO_PENDING_FLOW_ERROR =
  'No hay un proceso de autenticacion en curso. Vuelve a iniciar sesion.';

/**
 * Prompt de segundo factor (ruta `2fa`).
 *
 * Orquesta DOS flujos a traves del `AuthService` (steering: la UI no contiene
 * logica de negocio; toda la logica del Client SDK vive en el servicio):
 *
 * - Cliente (R3.2, R3.3): recibe el `MultiFactorResolver` por el estado de
 *   navegacion, presenta un campo accesible para el codigo TOTP de 6 digitos y
 *   completa el 2FA con `resolveTotpSignIn` (que usa
 *   `TotpMultiFactorGenerator.assertionForSignIn`).
 * - Interno (R4.3, R4.6): al inicializarse maneja el retorno del redirect de F5
 *   mediante `completeRedirectSignIn` (`getRedirectResult`). El 2FA corporativo
 *   ya ocurrio en F5 APM; aqui solo se recupera el resultado.
 *
 * En ambos flujos, tras obtener el usuario autenticado se obtiene el Firebase ID
 * token (`getIdToken`) y se establece la sesion de backend (`establishSession`,
 * cookie `Secure`/`HttpOnly`/`SameSite`) antes de navegar al dashboard. Ningun
 * token se almacena en el componente ni en localStorage (R5.6).
 *
 * Todos los fallos se exponen con un mensaje GENERICO que no revela que factor
 * fallo (R3.4, R3.5).
 */
@Component({
  selector: 'app-two-factor',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ReactiveFormsModule],
  template: `
    <section aria-label="Segundo factor de autenticacion">
      <h1>Verificacion en dos pasos</h1>

      @if (isProcessingRedirect()) {
        <p role="status" aria-live="polite">Completando el inicio de sesion...</p>
      } @else if (hasPendingClientFlow()) {
        <form (ngSubmit)="submitTotp()" novalidate>
          <label for="totp-code">
            Ingresa el codigo de 6 digitos de tu aplicacion de autenticacion
          </label>
          <input
            id="totp-code"
            name="totp-code"
            type="text"
            inputmode="numeric"
            autocomplete="one-time-code"
            [maxlength]="totpLength"
            [attr.aria-invalid]="hasError() ? 'true' : null"
            aria-describedby="totp-help totp-error"
            [formControl]="totpControl"
          />
          <p id="totp-help">Codigo numerico de {{ totpLength }} digitos.</p>

          <button type="submit" [disabled]="totpControl.invalid || isSubmitting()">
            Verificar
          </button>
        </form>
      }

      @if (hasError()) {
        <p id="totp-error" role="alert" aria-live="assertive">{{ errorMessage() }}</p>
      }
    </section>
  `,
})
export class TwoFactorComponent implements OnInit {
  private readonly authService = inject(AuthService);
  private readonly router = inject(Router);

  /** Resolver de MFA del flujo Cliente, recibido por el estado de navegacion. */
  private resolver: MultiFactorResolver | null = null;

  /** Expone la longitud del codigo al template para el `maxlength`. */
  protected readonly totpLength = TOTP_LENGTH;

  /** Campo del codigo TOTP: exactamente 6 digitos numericos (validacion de forma). */
  protected readonly totpControl = new FormControl('', {
    nonNullable: true,
    validators: [
      Validators.required,
      Validators.minLength(TOTP_LENGTH),
      Validators.maxLength(TOTP_LENGTH),
      Validators.pattern(TOTP_PATTERN),
    ],
  });

  /** `true` mientras se resuelve el retorno del redirect de Interno (R4.6). */
  protected readonly isProcessingRedirect = signal(false);

  /** `true` mientras se completa el 2FA del Cliente para evitar envios dobles. */
  protected readonly isSubmitting = signal(false);

  /** Mensaje de error generico a mostrar; cadena vacia cuando no hay error. */
  protected readonly errorMessage = signal('');

  /**
   * Captura el `MultiFactorResolver` del estado de navegacion (flujo Cliente).
   * Debe leerse en el constructor porque `getCurrentNavigation()` solo esta
   * disponible durante la navegacion que activa el componente.
   */
  constructor() {
    const state = this.router.getCurrentNavigation()?.extras.state as
      | TwoFactorNavigationState
      | undefined;
    this.resolver = state?.resolver ?? null;
  }

  /**
   * Al inicializar: si no hay resolver de Cliente, se asume el retorno del
   * redirect SAML del Interno y se intenta recuperar el resultado (R4.6).
   */
  ngOnInit(): void {
    if (this.resolver === null) {
      this.handleInternoRedirect();
    }
  }

  /** `true` si hay un flujo de Cliente pendiente (resolver disponible). */
  protected hasPendingClientFlow(): boolean {
    return this.resolver !== null;
  }

  /** `true` si hay un mensaje de error que mostrar. */
  protected hasError(): boolean {
    return this.errorMessage() !== '';
  }

  /**
   * Completa el segundo factor del Cliente con el codigo TOTP ingresado (R3.2,
   * R3.3). Delega en `AuthService.resolveTotpSignIn`; ante cualquier fallo
   * muestra un mensaje generico sin revelar el motivo (R3.4, R3.5).
   */
  protected submitTotp(): void {
    const resolver = this.resolver;
    if (resolver === null) {
      this.fail(NO_PENDING_FLOW_ERROR);
      return;
    }
    if (this.totpControl.invalid || this.isSubmitting()) {
      this.totpControl.markAsTouched();
      return;
    }

    const enrollmentId = this.firstTotpEnrollmentId(resolver);
    if (enrollmentId === null) {
      this.fail(GENERIC_AUTH_ERROR);
      return;
    }

    this.isSubmitting.set(true);
    this.errorMessage.set('');

    this.authService
      .resolveTotpSignIn(resolver, enrollmentId, this.totpControl.value)
      .subscribe({
        next: (credential) => this.establishSessionAndContinue(credential),
        error: () => {
          this.isSubmitting.set(false);
          this.fail(GENERIC_AUTH_ERROR);
        },
      });
  }

  /**
   * Maneja el retorno del redirect SAML del Interno (R4.6). Si no hay redirect
   * pendiente (`null`), no hay flujo valido en curso y se informa de forma
   * generica. Ante exito, continua con la obtencion del ID token y la sesion.
   */
  private handleInternoRedirect(): void {
    this.isProcessingRedirect.set(true);
    this.authService.completeRedirectSignIn().subscribe({
      next: (credential) => {
        if (credential === null) {
          this.isProcessingRedirect.set(false);
          this.fail(NO_PENDING_FLOW_ERROR);
          return;
        }
        this.establishSessionAndContinue(credential);
      },
      error: () => {
        this.isProcessingRedirect.set(false);
        this.fail(GENERIC_AUTH_ERROR);
      },
    });
  }

  /**
   * Obtiene el Firebase ID token del usuario autenticado y establece la sesion
   * de backend (cookie HttpOnly) antes de navegar al dashboard. Comun a ambos
   * flujos. Ante cualquier fallo, mensaje generico (R3.4, R3.5).
   *
   * @param credential credenciales del usuario autenticado por el Client SDK.
   */
  private establishSessionAndContinue(credential: UserCredential): void {
    const user: User = credential.user;
    this.authService.getIdToken(user).subscribe({
      next: (idToken) => {
        this.authService.establishSession(idToken).subscribe({
          next: () => {
            this.isSubmitting.set(false);
            this.isProcessingRedirect.set(false);
            void this.router.navigate(['/dashboard']);
          },
          error: () => {
            this.isSubmitting.set(false);
            this.isProcessingRedirect.set(false);
            this.fail(GENERIC_AUTH_ERROR);
          },
        });
      },
      error: () => {
        this.isSubmitting.set(false);
        this.isProcessingRedirect.set(false);
        this.fail(GENERIC_AUTH_ERROR);
      },
    });
  }

  /**
   * Obtiene el id del primer factor TOTP inscrito en el resolver. La PoC usa
   * TOTP como unico segundo factor para Clientes (R3.2); se toma el primer hint
   * disponible.
   *
   * @param resolver resolver de MFA del flujo Cliente.
   * @returns el `uid` del factor inscrito, o `null` si no hay ninguno.
   */
  private firstTotpEnrollmentId(resolver: MultiFactorResolver): string | null {
    const [hint] = resolver.hints;
    return hint?.uid ?? null;
  }

  /**
   * Establece un mensaje de error generico. Centraliza el manejo para garantizar
   * que nunca se exponga que factor fallo (R3.4, R3.5).
   *
   * @param message mensaje generico a mostrar al usuario.
   */
  private fail(message: string): void {
    this.errorMessage.set(message);
  }
}
