import { TestBed } from '@angular/core/testing';
import { Router, type UrlTree, type ActivatedRouteSnapshot, type RouterStateSnapshot } from '@angular/router';
import { of, throwError } from 'rxjs';

import { authGuard } from './auth.guard';
import { AuthService, type DashboardResponse } from './auth.service';

/**
 * Pruebas del guard funcional `authGuard` (R6.2, R6.3). Se mockea `AuthService`
 * para aislar la decision de autorizacion server-side; el guard solo refleja
 * la respuesta del backend.
 */
describe('authGuard', () => {
  const dummyRoute = {} as ActivatedRouteSnapshot;
  const dummyState = {} as RouterStateSnapshot;
  const loginUrlTree = {} as UrlTree;

  const authServiceSpy = { getDashboard: jest.fn() };
  const routerSpy = { createUrlTree: jest.fn(() => loginUrlTree) };

  const runGuard = () =>
    TestBed.runInInjectionContext(() => authGuard(dummyRoute, dummyState));

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        { provide: AuthService, useValue: authServiceSpy },
        { provide: Router, useValue: routerSpy },
      ],
    });
    jest.clearAllMocks();
  });

  it('permite la activacion cuando el backend confirma la sesion (R6.2)', (done) => {
    // Given
    const dashboard: DashboardResponse = {
      display_name: 'Usuario',
      user_type: 'cliente',
      claims: [],
    };
    authServiceSpy.getDashboard.mockReturnValue(of(dashboard));

    // When
    (runGuard() as ReturnType<typeof of>).subscribe((result: unknown) => {
      // Then
      expect(result).toBe(true);
      expect(routerSpy.createUrlTree).toHaveBeenCalledWith(['login']);
      done();
    });
  });

  it('redirige a login cuando el backend rechaza la sesion (R6.3)', (done) => {
    // Given
    authServiceSpy.getDashboard.mockReturnValue(
      throwError(() => new Error('401')),
    );

    // When
    (runGuard() as ReturnType<typeof of>).subscribe((result: unknown) => {
      // Then
      expect(result).toBe(loginUrlTree);
      done();
    });
  });
});
