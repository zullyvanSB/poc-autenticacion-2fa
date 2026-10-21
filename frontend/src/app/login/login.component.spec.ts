import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { of, throwError } from 'rxjs';

import type { User, UserCredential } from 'firebase/auth';

import { LoginComponent } from './login.component';
import { AuthService, type RouteResponse } from '../core/auth.service';

/**
 * Pruebas del `LoginComponent` enfocadas en la orquestacion de rutas de
 * `onSubmitIdentifier()` y, en particular, en el camino B (corporativo) que
 * dispara el login de Google con popup (POCA-007). Se mockean `AuthService`
 * (frontera con el Firebase Client SDK) y `Router` (navegacion): las pruebas
 * no ejercitan el SDK real ni el backend, solo la logica de orquestacion del
 * componente. `completeRedirectSignIn()` devuelve `of(null)` para que el
 * `ngOnInit` no interfiera con los casos bajo prueba.
 */
describe('LoginComponent', () => {
  /** Mensaje generico de ingreso que el componente muestra ante fallos (R1.6). */
  const GENERIC_LOGIN_ERROR =
    'No fue posible procesar el ingreso. Intenta nuevamente.';

  /** Spy de `AuthService`: cada metodo consumido por el componente es un mock. */
  const authServiceSpy = {
    resolveRoute: jest.fn(),
    signInInterno: jest.fn(),
    signInGoogle: jest.fn(),
    getIdToken: jest.fn(),
    establishSession: jest.fn(),
    completeRedirectSignIn: jest.fn(),
  };

  /** Spy de `Router`: solo se observa `navigate` hacia el dashboard. */
  const routerSpy = { navigate: jest.fn() };

  /**
   * Construye una `RouteResponse` del backend para un `route_type` dado,
   * completando los campos del contrato que el componente no inspecciona.
   *
   * @param routeType tipo de ruta resuelto por el backend.
   * @returns respuesta de ruta tipada.
   */
  const routeResponse = (
    routeType: RouteResponse['route_type'],
  ): RouteResponse => ({
    route_type: routeType,
    login_method: 'x',
    correlation_id: 'c-1',
  });

  /**
   * Crea el componente con el valor del identificador ya escrito y devuelve
   * la instancia lista para invocar `onSubmitIdentifier()`.
   *
   * @param identifierValue valor a colocar en el control de identificador.
   * @returns instancia del `LoginComponent` bajo prueba.
   */
  const createComponent = (identifierValue: string): LoginComponent => {
    const fixture = TestBed.createComponent(LoginComponent);
    const component = fixture.componentInstance;
    fixture.detectChanges(); // dispara ngOnInit (completeRedirectSignIn -> of(null))
    (
      component as unknown as { identifier: { setValue: (v: string) => void } }
    ).identifier.setValue(identifierValue);
    return component;
  };

  /**
   * Lee un signal protegido del componente por nombre, con tipo explicito,
   * sin exponer `any` (los signals son `protected`; las pruebas los leen via
   * un cast acotado y tipado).
   *
   * @param component instancia del componente.
   * @param name nombre del signal a leer.
   * @returns el valor actual del signal.
   */
  const readSignal = <T>(component: LoginComponent, name: string): T =>
    (component as unknown as Record<string, () => T>)[name]();

  /**
   * Invoca `onSubmitIdentifier()` (handler `protected` del componente) a traves
   * de un cast acotado y tipado, sin exponer `any`.
   *
   * @param component instancia del componente bajo prueba.
   */
  const submitIdentifier = (component: LoginComponent): void => {
    (component as unknown as { onSubmitIdentifier: () => void }).onSubmitIdentifier();
  };

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [LoginComponent],
      providers: [
        { provide: AuthService, useValue: authServiceSpy },
        { provide: Router, useValue: routerSpy },
      ],
    });
    jest.clearAllMocks();
    // Por defecto, ngOnInit no encuentra redirect pendiente.
    authServiceSpy.completeRedirectSignIn.mockReturnValue(of(null));
  });

  it('se crea', () => {
    // Given / When
    const component = createComponent('cliente@test.com');

    // Then
    expect(component).toBeTruthy();
  });

  describe('no-regresion de los caminos existentes (FR-7)', () => {
    it('ruta interno: onSubmitIdentifier invoca signInInterno() (camino A, SAML)', () => {
      // Given
      authServiceSpy.resolveRoute.mockReturnValue(of(routeResponse('interno')));
      authServiceSpy.signInInterno.mockReturnValue(of(null)); // no se completa la sesion aqui
      const component = createComponent('12345678');

      // When
      submitIdentifier(component);

      // Then
      expect(authServiceSpy.signInInterno).toHaveBeenCalledTimes(1);
      expect(authServiceSpy.signInGoogle).not.toHaveBeenCalled();
      expect(readSignal<string>(component, 'phase')).not.toBe('password');
    });

    it('ruta cliente: onSubmitIdentifier avanza a la fase password (camino C)', () => {
      // Given
      authServiceSpy.resolveRoute.mockReturnValue(of(routeResponse('cliente')));
      const component = createComponent('cliente@test.com');

      // When
      submitIdentifier(component);

      // Then
      expect(readSignal<string>(component, 'phase')).toBe('password');
      expect(authServiceSpy.signInInterno).not.toHaveBeenCalled();
      expect(authServiceSpy.signInGoogle).not.toHaveBeenCalled();
      expect(readSignal<boolean>(component, 'submitting')).toBe(false);
    });
  });

  describe('camino B corporativo — disparo automatico (FR-1, FR-8)', () => {
    it('ruta corporativo: invoca signInGoogle() exactamente una vez y NO avanza a password', () => {
      // Given: el disparo ocurre; dejamos el observable sin emitir para aislar el ruteo.
      authServiceSpy.resolveRoute.mockReturnValue(
        of(routeResponse('corporativo')),
      );
      authServiceSpy.signInGoogle.mockReturnValue(of()); // no emite: solo verificamos el disparo
      const component = createComponent('empleado@empresa.com');

      // When
      submitIdentifier(component);

      // Then
      expect(authServiceSpy.signInGoogle).toHaveBeenCalledTimes(1);
      expect(authServiceSpy.signInInterno).not.toHaveBeenCalled();
      expect(readSignal<string>(component, 'phase')).not.toBe('password');
    });
  });

  describe('camino B corporativo — manejo del UserCredential (FR-3)', () => {
    it('con UserCredential del popup: activa processingRedirect, establece sesion y navega al dashboard', () => {
      // Given
      const user = { uid: 'u-corp' } as unknown as User;
      const credential = { user } as unknown as UserCredential;
      authServiceSpy.resolveRoute.mockReturnValue(
        of(routeResponse('corporativo')),
      );
      authServiceSpy.signInGoogle.mockReturnValue(of(credential));
      authServiceSpy.getIdToken.mockReturnValue(of('id-token-corp'));
      authServiceSpy.establishSession.mockReturnValue(of(void 0));
      const component = createComponent('empleado@empresa.com');

      // When
      submitIdentifier(component);

      // Then
      expect(readSignal<boolean>(component, 'processingRedirect')).toBe(true);
      expect(authServiceSpy.getIdToken).toHaveBeenCalledWith(user, true);
      expect(authServiceSpy.establishSession).toHaveBeenCalledWith(
        'id-token-corp',
      );
      expect(routerSpy.navigate).toHaveBeenCalledWith(['/dashboard']);
      expect(readSignal<boolean>(component, 'submitting')).toBe(false);
    });
  });

  describe('camino B corporativo — fallo del popup (FR-4, edge cases)', () => {
    it('popup cerrado por el usuario: muestra GENERIC_LOGIN_ERROR, submitting=false y permanece en identifier', () => {
      // Given
      authServiceSpy.resolveRoute.mockReturnValue(
        of(routeResponse('corporativo')),
      );
      authServiceSpy.signInGoogle.mockReturnValue(
        throwError(() => ({ code: 'auth/popup-closed-by-user' })),
      );
      const component = createComponent('empleado@empresa.com');

      // When
      submitIdentifier(component);

      // Then
      expect(readSignal<string | null>(component, 'backendError')).toBe(
        GENERIC_LOGIN_ERROR,
      );
      expect(readSignal<boolean>(component, 'submitting')).toBe(false);
      expect(readSignal<string>(component, 'phase')).toBe('identifier');
      expect(routerSpy.navigate).not.toHaveBeenCalled();
      expect(authServiceSpy.getIdToken).not.toHaveBeenCalled();
    });

    it('popup bloqueado por el navegador: mismo manejo generico (sin revelar el motivo)', () => {
      // Given
      authServiceSpy.resolveRoute.mockReturnValue(
        of(routeResponse('corporativo')),
      );
      authServiceSpy.signInGoogle.mockReturnValue(
        throwError(() => ({ code: 'auth/popup-blocked' })),
      );
      const component = createComponent('empleado@empresa.com');

      // When
      submitIdentifier(component);

      // Then
      expect(readSignal<string | null>(component, 'backendError')).toBe(
        GENERIC_LOGIN_ERROR,
      );
      expect(readSignal<boolean>(component, 'submitting')).toBe(false);
      expect(readSignal<string>(component, 'phase')).toBe('identifier');
    });
  });
});
