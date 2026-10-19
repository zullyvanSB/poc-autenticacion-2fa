import {
  ChangeDetectionStrategy,
  Component,
  type OnInit,
  inject,
  signal,
} from '@angular/core';
import { Router } from '@angular/router';

import {
  AuthService,
  type DashboardResponse,
  type RouteType,
} from '../core/auth.service';

/**
 * Etiquetas visibles del tipo de usuario (R6.1): el contrato del backend expone
 * `cliente`/`interno`; la UI muestra "Cliente"/"Interno".
 */
const USER_TYPE_LABELS: Readonly<Record<RouteType, string>> = {
  cliente: 'Cliente',
  interno: 'Interno',
};

/**
 * Mensaje de ausencia de datos (R6.6): cuando el token no trae claims, se
 * muestra una indicacion explicita en lugar de campos vacios sin contexto.
 */
const NO_CLAIMS_MESSAGE =
  'No hay datos disponibles para mostrar en este momento.';

/**
 * Mensaje generico cuando el dashboard no puede cargarse (sesion invalida,
 * timeout o error del backend). No revela el motivo tecnico; la proteccion de
 * la ruta la ejerce `authGuard`, aqui solo se refleja el estado al usuario.
 */
const DASHBOARD_LOAD_ERROR =
  'No fue posible cargar la informacion. Vuelve a iniciar sesion.';

/**
 * Recurso protegido (ruta `dashboard`, R6).
 *
 * Al inicializarse consulta el recurso protegido via `AuthService.getDashboard`
 * (el backend valida la sesion y es la fuente de verdad) y presenta el nombre
 * del usuario, el tipo de usuario como "Cliente"/"Interno" y la lista de claims
 * ya enmascarados por el backend (R6.1). El componente NO desenmascara ni aplica
 * logica de negocio: solo orquesta UI (steering: separacion de capas).
 *
 * Si no hay claims disponibles muestra un mensaje de ausencia de datos y evita
 * renderizar campos vacios sin indicacion (R6.6). La accion de cierre de sesion
 * invoca `AuthService.logout` (que revoca la sesion server-side) y redirige a
 * `login`; la redireccion ocurre tanto en exito como en error, de modo que la
 * sesion del navegador siempre termina en la pantalla de ingreso (R6.5).
 */
@Component({
  selector: 'app-dashboard',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="dashboard" aria-label="Dashboard">
      <header class="dashboard__header">
        <h1 class="dashboard__title">Portal</h1>
        <button
          type="button"
          class="dashboard__logout"
          (click)="onLogout()"
          [disabled]="loggingOut()"
        >
          @if (loggingOut()) {
            Cerrando sesion...
          } @else {
            Cerrar sesion
          }
        </button>
      </header>

      @if (loading()) {
        <p class="dashboard__status" role="status">Cargando informacion...</p>
      } @else if (loadError()) {
        <p class="dashboard__status dashboard__status--error" role="alert">
          {{ loadError() }}
        </p>
      } @else if (data(); as dashboard) {
        <dl class="dashboard__identity">
          <dt class="dashboard__term">Nombre</dt>
          <dd class="dashboard__value">{{ dashboard.display_name }}</dd>
          <dt class="dashboard__term">Tipo de usuario</dt>
          <dd class="dashboard__value">{{ userTypeLabel(dashboard.user_type) }}</dd>
        </dl>

        <h2 class="dashboard__subtitle">Claims</h2>
        @if (dashboard.claims.length > 0) {
          <ul class="dashboard__claims" aria-label="Claims del usuario">
            @for (claim of dashboard.claims; track claim.name) {
              <li class="dashboard__claim">
                <span class="dashboard__claim-name">{{ claim.name }}</span>
                <span class="dashboard__claim-value">{{ claim.value }}</span>
              </li>
            }
          </ul>
        } @else {
          <p class="dashboard__status" role="status">{{ noClaimsMessage }}</p>
        }
      }
    </section>
  `,
  styles: [
    `
      .dashboard {
        max-width: 36rem;
        margin: 3rem auto;
        padding: 0 1rem;
        display: flex;
        flex-direction: column;
        gap: 1.25rem;
      }
      .dashboard__header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
      }
      .dashboard__title {
        margin: 0;
        font-size: 1.5rem;
      }
      .dashboard__subtitle {
        margin: 0;
        font-size: 1.125rem;
      }
      .dashboard__logout {
        padding: 0.5rem 1rem;
        font-size: 0.95rem;
        cursor: pointer;
        border-radius: 0.375rem;
      }
      .dashboard__logout:disabled {
        cursor: progress;
        opacity: 0.7;
      }
      .dashboard__identity {
        display: grid;
        grid-template-columns: auto 1fr;
        gap: 0.5rem 1rem;
        margin: 0;
      }
      .dashboard__term {
        font-weight: 600;
      }
      .dashboard__value {
        margin: 0;
      }
      .dashboard__claims {
        list-style: none;
        margin: 0;
        padding: 0;
        display: flex;
        flex-direction: column;
        gap: 0.5rem;
      }
      .dashboard__claim {
        display: flex;
        justify-content: space-between;
        gap: 1rem;
        padding: 0.5rem 0.75rem;
        border: 1px solid currentColor;
        border-radius: 0.375rem;
      }
      .dashboard__claim-name {
        font-weight: 600;
      }
      .dashboard__status {
        margin: 0;
        font-size: 0.95rem;
      }
      .dashboard__status--error {
        color: #b00020;
      }
    `,
  ],
})
export class DashboardComponent implements OnInit {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  /** Mensaje de ausencia de datos expuesto al template (R6.6). */
  protected readonly noClaimsMessage = NO_CLAIMS_MESSAGE;

  /** Datos del dashboard resueltos por el backend; `null` hasta cargar. */
  protected readonly data = signal<DashboardResponse | null>(null);

  /** `true` mientras la consulta inicial al backend esta en curso. */
  protected readonly loading = signal(true);

  /** Mensaje generico si la carga falla (sesion invalida, timeout, error). */
  protected readonly loadError = signal<string | null>(null);

  /** `true` mientras el cierre de sesion esta en curso (evita doble click). */
  protected readonly loggingOut = signal(false);

  /**
   * Carga los datos del recurso protegido al inicializarse (R6.1). El backend
   * valida la sesion; ante error se muestra un mensaje generico sin exponer el
   * motivo tecnico.
   */
  ngOnInit(): void {
    this.auth.getDashboard().subscribe({
      next: (dashboard: DashboardResponse) => {
        this.data.set(dashboard);
        this.loading.set(false);
      },
      error: () => {
        this.loadError.set(DASHBOARD_LOAD_ERROR);
        this.loading.set(false);
      },
    });
  }

  /**
   * Traduce el tipo de usuario del contrato (`cliente`/`interno`) a la etiqueta
   * visible "Cliente"/"Interno" (R6.1).
   *
   * @param userType tipo de usuario devuelto por el backend.
   * @returns la etiqueta visible correspondiente.
   */
  protected userTypeLabel(userType: RouteType): string {
    return USER_TYPE_LABELS[userType];
  }

  /**
   * Cierra la sesion (R6.5): invoca `AuthService.logout` (revoca la sesion
   * server-side y borra la cookie) y redirige a `login`. La redireccion ocurre
   * tanto en exito como en error para que el navegador siempre termine fuera
   * del recurso protegido.
   */
  protected onLogout(): void {
    if (this.loggingOut()) {
      return;
    }
    this.loggingOut.set(true);
    this.auth.logout().subscribe({
      next: () => {
        void this.router.navigate(['/login']);
      },
      error: () => {
        void this.router.navigate(['/login']);
      },
    });
  }
}
