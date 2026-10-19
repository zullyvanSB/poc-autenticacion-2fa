import type { Routes } from '@angular/router';

import { authGuard } from './core/auth.guard';

/**
 * Enrutamiento de la PoC: ingreso identifier-first (`login`), prompt de 2FA
 * (`2fa`) y recurso protegido (`dashboard`). Las rutas usan carga diferida
 * (lazy) de componentes standalone.
 *
 * La ruta `dashboard` esta protegida por `authGuard` (tarea 11.1): la sesion
 * se verifica contra el backend (fuente de verdad server-side) antes de activar
 * la ruta.
 */
export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'login' },
  {
    path: 'login',
    loadComponent: () =>
      import('./login/login.component').then((m) => m.LoginComponent),
  },
  {
    path: '2fa',
    loadComponent: () =>
      import('./two-factor/two-factor.component').then((m) => m.TwoFactorComponent),
  },
  {
    path: 'dashboard',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./dashboard/dashboard.component').then((m) => m.DashboardComponent),
  },
  { path: '**', redirectTo: 'login' },
];
