import type { AppEnvironment } from './environment.model';
import { buildEnvironment } from './runtime-config';

/**
 * Entorno PRODUCCION (GCP: Firebase Hosting).
 *
 * La URL del backend (Cloud Run) se resuelve por entorno en tiempo de
 * ejecucion (R9.6), sin IPs ni URLs estaticas embebidas y sin valor por
 * defecto (R9.7). El hosting provee `window.__APP_CONFIG__.apiBaseUrl`.
 */
export const environment: AppEnvironment = buildEnvironment(true);
