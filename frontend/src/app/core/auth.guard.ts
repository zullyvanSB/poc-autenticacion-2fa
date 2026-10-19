import { inject } from '@angular/core';
import { Router, type CanActivateFn, type UrlTree } from '@angular/router';
import { type Observable, of } from 'rxjs';
import { catchError, map } from 'rxjs/operators';

import { AuthService } from './auth.service';

/**
 * Guard funcional que protege la ruta del dashboard (R6.2, R6.3).
 *
 * La autorizacion es responsabilidad del servidor: este guard NO decide
 * localmente si el usuario esta autenticado. Consulta el recurso protegido
 * `/api/v1/dashboard` como unica fuente de verdad; el backend valida la cookie
 * de sesion (`verify_session_cookie`). El guard se limita a reflejar la
 * decision del backend (steering: la autorizacion vive en el backend, nunca en
 * el cliente).
 *
 * - Si `getDashboard()` responde con exito (200), la sesion es valida y se
 *   permite la activacion de la ruta.
 * - Si la consulta falla (401 u otro error), se redirige a `login` devolviendo
 *   un `UrlTree`, sin exponer datos del recurso protegido.
 *
 * @returns `true` si la sesion server-side es valida; en caso contrario un
 *   `UrlTree` que redirige a `login`.
 */
export const authGuard: CanActivateFn = (): Observable<boolean | UrlTree> => {
  const authService = inject(AuthService);
  const router = inject(Router);
  const loginUrlTree = router.createUrlTree(['login']);

  return authService.getDashboard().pipe(
    map((): boolean | UrlTree => true),
    catchError((): Observable<boolean | UrlTree> => of(loginUrlTree)),
  );
};
