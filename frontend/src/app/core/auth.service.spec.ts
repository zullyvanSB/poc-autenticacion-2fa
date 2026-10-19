import { TestBed } from '@angular/core/testing';
import { HttpClient } from '@angular/common/http';

import { AuthService, ConfigurationError } from './auth.service';
import { environment } from '../../environments/environment';

// Se mockean los modulos de Firebase: estas pruebas de humo no ejercitan el
// SDK real, solo la orquestacion y el bloqueo por configuracion no resuelta.
jest.mock('firebase/app', () => ({
  initializeApp: jest.fn(() => ({})),
}));
jest.mock('firebase/auth', () => ({
  getAuth: jest.fn(() => ({})),
  getMultiFactorResolver: jest.fn(),
  getRedirectResult: jest.fn(),
  SAMLAuthProvider: jest.fn(),
  signInWithEmailAndPassword: jest.fn(),
  signInWithRedirect: jest.fn(),
  TotpMultiFactorGenerator: { assertionForSignIn: jest.fn() },
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
});
