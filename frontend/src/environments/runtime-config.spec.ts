import { resolveFirebaseConfig } from './runtime-config';

/**
 * Pruebas de resolucion de la configuracion web publica de Firebase por
 * runtime-config. Verifican el tratamiento del nuevo campo opcional
 * `corporateHostedDomain` (dominio `hd` del camino B), coherente con como se
 * resuelven hoy `tenantId`/`samlProviderId`: se incluye solo si resuelve a
 * cadena no vacia; vacio o placeholder `__...__` se descarta.
 */
describe('resolveFirebaseConfig — campo corporateHostedDomain (hd)', () => {
  const baseConfig = {
    apiKey: 'k',
    authDomain: 'd.firebaseapp.com',
    projectId: 'p',
    samlProviderId: 'saml.f5apm',
  } as const;

  afterEach(() => {
    delete (window as { __APP_CONFIG__?: unknown }).__APP_CONFIG__;
  });

  it('incluye corporateHostedDomain cuando resuelve a cadena no vacia', () => {
    // Given
    (window as { __APP_CONFIG__?: unknown }).__APP_CONFIG__ = {
      firebase: { ...baseConfig, corporateHostedDomain: 'empresa.com' },
    };

    // When
    const resolved = resolveFirebaseConfig();

    // Then
    expect(resolved).not.toBeNull();
    expect(resolved?.corporateHostedDomain).toBe('empresa.com');
  });

  it('recorta espacios del corporateHostedDomain resuelto', () => {
    // Given
    (window as { __APP_CONFIG__?: unknown }).__APP_CONFIG__ = {
      firebase: { ...baseConfig, corporateHostedDomain: '  empresa.com  ' },
    };

    // When
    const resolved = resolveFirebaseConfig();

    // Then
    expect(resolved?.corporateHostedDomain).toBe('empresa.com');
  });

  it('omite corporateHostedDomain cuando es cadena vacia', () => {
    // Given
    (window as { __APP_CONFIG__?: unknown }).__APP_CONFIG__ = {
      firebase: { ...baseConfig, corporateHostedDomain: '' },
    };

    // When
    const resolved = resolveFirebaseConfig();

    // Then
    expect(resolved).not.toBeNull();
    expect(resolved?.corporateHostedDomain).toBeUndefined();
  });

  it('omite corporateHostedDomain cuando es un placeholder sin sustituir', () => {
    // Given
    (window as { __APP_CONFIG__?: unknown }).__APP_CONFIG__ = {
      firebase: { ...baseConfig, corporateHostedDomain: '__CORPORATE_HOSTED_DOMAIN__' },
    };

    // When
    const resolved = resolveFirebaseConfig();

    // Then
    expect(resolved?.corporateHostedDomain).toBeUndefined();
  });

  it('omite corporateHostedDomain cuando el campo esta ausente', () => {
    // Given: config valida sin el campo hd.
    (window as { __APP_CONFIG__?: unknown }).__APP_CONFIG__ = {
      firebase: { ...baseConfig },
    };

    // When
    const resolved = resolveFirebaseConfig();

    // Then
    expect(resolved).not.toBeNull();
    expect(resolved?.corporateHostedDomain).toBeUndefined();
  });

  it('no forma parte del null-check de campos obligatorios (ausente ⇒ config valida)', () => {
    // Given: campos obligatorios presentes, hd ausente.
    (window as { __APP_CONFIG__?: unknown }).__APP_CONFIG__ = {
      firebase: { ...baseConfig },
    };

    // When
    const resolved = resolveFirebaseConfig();

    // Then: la config resuelve igual (hd opcional no bloquea cliente/interno).
    expect(resolved).toEqual(baseConfig);
  });
});
