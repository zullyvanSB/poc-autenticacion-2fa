import type { AppEnvironment } from './environment.model';
import { buildEnvironment } from './runtime-config';

/**
 * Entorno LOCAL/desarrollo.
 *
 * La URL del backend NO se embebe aqui: se resuelve por entorno en tiempo de
 * ejecucion (R9.6). Si no esta resuelta, el valor es cadena vacia y el
 * `AuthService` bloquea las llamadas exponiendo un error de configuracion
 * (R9.7). No hay direccion por defecto.
 */
export const environment: AppEnvironment = buildEnvironment(false);
