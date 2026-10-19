import type { Config } from 'jest';

/**
 * Configuracion de Jest para el Frontend_Angular (steering: Jest como runner
 * de pruebas de Angular). Las pruebas unitarias de componentes y servicios se
 * agregan en tareas posteriores (10.x, 11.x).
 */
const config: Config = {
  preset: 'jest-preset-angular',
  setupFilesAfterEnv: ['<rootDir>/src/setup-jest.ts'],
  testMatch: ['<rootDir>/src/**/*.spec.ts'],
  moduleFileExtensions: ['ts', 'html', 'js', 'json', 'mjs'],
  collectCoverageFrom: [
    'src/**/*.ts',
    '!src/main.ts',
    '!src/setup-jest.ts',
    '!src/**/*.d.ts',
  ],
  coverageDirectory: '<rootDir>/coverage',
};

export default config;
