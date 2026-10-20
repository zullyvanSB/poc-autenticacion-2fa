import { TestBed } from '@angular/core/testing';
import { HttpClient } from '@angular/common/http';

import { AuthService, ConfigurationError } from './auth.service';
import { environment } from '../../environments/environment';

// Se mockean los modulos de Firebase: estas pruebas de humo no ejercitan el
// SDK real, solo la orquestacion y el bloqueo por configuracion no resuelta.
jest.mock('firebase/app', () => ({
  initializeApp: jest.fn(() => ({})),
}));
// `GoogleAuthProvider` se mockea como clase: cada instancia expone un espia
// `setCustomParameters` para verificar el `{ hd }` aplicado en `signInGoogle`.
const googleSetCustomParameters = jest.fn();
jest.mock('firebase/auth', () => ({
  getAuth: jest.fn(() => ({})),
  getMultiFactorResolver: jest.fn(),
  getRedirectResult: jest.fn(),
  multiFactor: jest.fn(),
  GoogleAuthProvider: jest.fn().mockImplementation(() => ({
    setCustomParameters: googleSetCustomParameters,
  })),
  SAMLAuthProvider: jest.fn(),
  signInWithEmailAndPassword: jest.fn(),
  signInWithPopup: jest.fn(),
  signInWithRedirect: jest.fn(),
  TotpMultiFactorGenerator: {
    assertionForSignIn: jest.fn(),
    assertionForEnrollment: jest.fn(),
    generateSecret: jest.fn(),
  },
}));

describe('AuthService', () => {
  let service: AuthService;
  const httpSpy = {
    post: jest.fn(),
    get: jest.fn(),
  };

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [AuthService, { provide: HttpClient, useValue: httpSpy }],
    });
    service = TestBed.inject(AuthService);
    jest.clearAllMocks();
  });

  it('se crea', () => {
    // Then
    expect(service).toBeTruthy();
  });

  it('bloquea las llamadas al backend si no hay URL resuelta por entorno (R9.7)', (done) => {
    // Given
    (environment as { apiBaseUrl: string }).apiBaseUrl = '';

    // When
    service.resolveRoute('cliente@test.com').subscribe({
      // Then
      next: () => done.fail('no debia emitir'),
      error: (error: unknown) => {
        expect(error).toBeInstanceOf(ConfigurationError);
        expect(httpSpy.post).not.toHaveBeenCalled();
        done();
      },
    });
  });

  it('llama a /auth/route con withCredentials cuando hay URL resuelta', () => {
    // Given
    (environment as { apiBaseUrl: string }).apiBaseUrl = 'https://api.example.test/api/v1';
    httpSpy.post.mockReturnValue({ pipe: jest.fn().mockReturnValue({ subscribe: jest.fn() }) });

    // When
    service.resolveRoute('cliente@test.com').subscribe({ error: () => undefined });

    // Then
    expect(httpSpy.post).toHaveBeenCalledWith(
      'https://api.example.test/api/v1/auth/route',
      { identifier: 'cliente@test.com' },
      { withCredentials: true },
    );
  });

  it('el flujo Interno usa signInWithPopup (no redirect) y emite el UserCredential', (done) => {
    // Given: config de Firebase resuelta y popup que resuelve con un credential.
    (environment as { firebase: unknown }).firebase = {
      apiKey: 'k',
      authDomain: 'd.firebaseapp.com',
      projectId: 'p',
      samlProviderId: 'saml.f5apm',
    };
    const fakeCredential = { user: { uid: 'u-interno' } };
    const { signInWithPopup, signInWithRedirect } =
      jest.requireMock('firebase/auth') as {
        signInWithPopup: jest.Mock;
        signInWithRedirect: jest.Mock;
      };
    signInWithPopup.mockResolvedValue(fakeCredential);

    // When
    service.signInInterno().subscribe({
      // Then: resuelve por popup, nunca por redirect.
      next: (credential) => {
        expect(signInWithPopup).toHaveBeenCalledTimes(1);
        expect(signInWithRedirect).not.toHaveBeenCalled();
        expect(credential).toBe(fakeCredential);
        done();
      },
      error: () => done.fail('no debia emitir error'),
    });
  });

  it('no inicializa Firebase si la config no esta resuelta (R9.7)', (done) => {
    // Given
    (environment as { firebase: unknown }).firebase = null;

    // When
    service.signInInterno().subscribe({
      // Then
      next: () => done.fail('no debia emitir'),
      error: (error: unknown) => {
        expect(error).toBeInstanceOf(ConfigurationError);
        done();
      },
    });
  });

  describe('signInGoogle (camino B — corporativo)', () => {
    const corporateConfig = {
      apiKey: 'k',
      authDomain: 'd.firebaseapp.com',
      projectId: 'p',
      samlProviderId: 'saml.f5apm',
      corporateHostedDomain: 'empresa.com',
    } as const;

    it('instancia GoogleAuthProvider, aplica setCustomParameters({ hd }) y usa signInWithPopup (FR-1..FR-5)', (done) => {
      // Given: config corporativa resuelta y popup que resuelve con un credential.
      (environment as { firebase: unknown }).firebase = { ...corporateConfig };
      const fakeCredential = { user: { uid: 'u-corporativo' } };
      const { GoogleAuthProvider, signInWithPopup, signInWithRedirect } =
        jest.requireMock('firebase/auth') as {
          GoogleAuthProvider: jest.Mock;
          signInWithPopup: jest.Mock;
          signInWithRedirect: jest.Mock;
        };
      signInWithPopup.mockResolvedValue(fakeCredential);

      // When
      service.signInGoogle().subscribe({
        // Then: provider + setCustomParameters({ hd }) + popup (nunca redirect).
        next: (credential) => {
          expect(GoogleAuthProvider).toHaveBeenCalledTimes(1);
          expect(googleSetCustomParameters).toHaveBeenCalledWith({ hd: 'empresa.com' });
          expect(signInWithPopup).toHaveBeenCalledTimes(1);
          expect(signInWithRedirect).not.toHaveBeenCalled();
          expect(credential).toBe(fakeCredential);
          done();
        },
        error: () => done.fail('no debia emitir error'),
      });
    });

    it('propaga ConfigurationError y NO llama signInWithPopup si el hd no esta resuelto (FR-6, edge case)', (done) => {
      // Given: config valida pero sin corporateHostedDomain (campos obligatorios OK).
      (environment as { firebase: unknown }).firebase = {
        apiKey: 'k',
        authDomain: 'd.firebaseapp.com',
        projectId: 'p',
        samlProviderId: 'saml.f5apm',
      };
      const { signInWithPopup } = jest.requireMock('firebase/auth') as {
        signInWithPopup: jest.Mock;
      };

      // When
      service.signInGoogle().subscribe({
        // Then: fail-safe — error de config por el observable, sin disparar popup.
        next: () => done.fail('no debia emitir'),
        error: (error: unknown) => {
          expect(error).toBeInstanceOf(ConfigurationError);
          expect(signInWithPopup).not.toHaveBeenCalled();
          done();
        },
      });
    });

    it('propaga ConfigurationError si la config de Firebase no esta resuelta (R9.7)', (done) => {
      // Given
      (environment as { firebase: unknown }).firebase = null;
      const { signInWithPopup } = jest.requireMock('firebase/auth') as {
        signInWithPopup: jest.Mock;
      };

      // When
      service.signInGoogle().subscribe({
        // Then
        next: () => done.fail('no debia emitir'),
        error: (error: unknown) => {
          expect(error).toBeInstanceOf(ConfigurationError);
          expect(signInWithPopup).not.toHaveBeenCalled();
          done();
        },
      });
    });

    it('reutiliza getFirebaseAuth(): no reinicializa el SDK en una segunda llamada', () => {
      // Given: config corporativa resuelta; getAuth cuenta inicializaciones.
      (environment as { firebase: unknown }).firebase = { ...corporateConfig };
      const { getAuth, signInWithPopup } = jest.requireMock('firebase/auth') as {
        getAuth: jest.Mock;
        signInWithPopup: jest.Mock;
      };
      const { initializeApp } = jest.requireMock('firebase/app') as {
        initializeApp: jest.Mock;
      };
      signInWithPopup.mockResolvedValue({ user: { uid: 'u' } });

      // When: dos invocaciones consecutivas.
      service.signInGoogle().subscribe({ error: () => undefined });
      service.signInGoogle().subscribe({ error: () => undefined });

      // Then: el SDK se inicializa una sola vez (instancia perezosa reutilizada).
      expect(initializeApp).toHaveBeenCalledTimes(1);
      expect(getAuth).toHaveBeenCalledTimes(1);
    });
  });
});
