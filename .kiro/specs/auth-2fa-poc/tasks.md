# Implementation Plan

## Overview

Plan de implementación incremental para la PoC de autenticación y 2FA (TOTP) con experiencia identifier-first. Se construye en fases: primero el esqueleto de los tres componentes (Backend_FastAPI, Frontend_Angular, CLI_Gestion), luego la lógica central del backend (validación/enrutamiento, sesión, dashboard, logout, middleware transversal), después el frontend (login/2FA/dashboard/servicio/guard), los scripts de la CLI, las pruebas basadas en propiedades (Hypothesis) y unitarias, las pruebas de integración contra proveedores reales, la dockerización y configuración de despliegue, y finalmente la Guía Operativa (R8).

El stack es fijo según el diseño: Backend en Python 3.12+/FastAPI con Poetry, Pydantic v2 y firebase-admin; Frontend en Angular con HttpClient/RxJS/inject() y Firebase Client SDK; CLI en Python con firebase-admin. Todas las dependencias se fijan a versión exacta y se resuelven desde JFrog. No hay lógica de negocio en el frontend; la autorización es server-side.

Convención de pruebas de propiedad: cada test usa Hypothesis con mínimo 100 iteraciones y una etiqueta en comentario con el formato `Feature: auth-2fa-poc, Property {n}: {texto}`. Se mockean el Firebase Admin SDK y todo cliente HTTP saliente; nunca se consumen backends reales en pruebas unitarias.

## Tasks

- [x] 1. Preparar el esqueleto del proyecto (backend, frontend, CLI)
  - [x] 1.1 Inicializar el proyecto del Backend_FastAPI con Poetry
    - Crear la estructura por dominio (`src/auth/`, `src/dashboard/`, `src/common/`) según la separación de capas del diseño
    - Configurar `pyproject.toml` con Poetry apuntando a JFrog y dependencias fijadas a versión exacta (sin `^`/`~`/`*`/`latest`): `fastapi`, `uvicorn`, `pydantic` v2.x, `firebase-admin` 6.x, `python-json-logger`; dev: `pytest`, `hypothesis`, `httpx`
    - Crear la app FastAPI base con prefijo de rutas `/api/v1/` y habilitar la especificación OpenAPI como fuente de verdad del contrato
    - Añadir `.gitignore` (excluye `.idea`, `__pycache__`, `.pytest_cache`, `.env`, archivos de credenciales)
    - _Requirements: 9.3, 5.2_

  - [x] 1.2 Definir los modelos Pydantic v2 (DTO) del backend
    - Implementar `UserType`, `RouteRequest`, `RouteResponse`, `SessionRequest`, `MaskedClaim`, `DashboardResponse`, `ErrorResponse` con `ConfigDict(extra="forbid")` y `str_strip_whitespace=True` donde aplica
    - Aplicar restricciones de longitud/rango según el diseño (identifier 5–254, id_token 1–4096)
    - _Requirements: 2.3, 10.6, 6.1, 6.4_

  - [x] 1.3 Inicializar el proyecto del Frontend_Angular
    - Crear el proyecto con Angular CLI; configurar `.npmrc` apuntando a JFrog y fijar versiones exactas de `firebase` (Client SDK) y dev `jest`
    - Crear `environment.ts` y `environment.prod.ts` con la URL del backend resuelta por entorno (sin IPs ni URLs estáticas embebidas; sin default embebido)
    - Definir el enrutamiento de rutas `login`, `2fa`, `dashboard`
    - Añadir `.gitignore` (excluye `.idea`, `node_modules`, `.vscode`, `dist`)
    - _Requirements: 9.6, 9.7_

  - [x] 1.4 Inicializar el proyecto de la CLI_Gestion
    - Crear el paquete Python de scripts (`cli/`) con Poetry y `firebase-admin` fijado a versión exacta desde JFrog
    - Definir el esqueleto de parseo de argumentos estricto (argparse/Pydantic) para `crear_usuario`, `actualizar_usuario`, `eliminar_usuario`, rechazando claves no esperadas
    - Preparar la carga de credenciales desde `GOOGLE_APPLICATION_CREDENTIALS` o archivo fuera de control de versiones
    - _Requirements: 7.6, 7.8_

- [x] 2. Implementar el middleware transversal y la validación de arranque del backend
  - [x] 2.1 Implementar `CorrelationIdMiddleware`
    - Si falta `X-Correlation-ID`, generar un UUID v4 y asociarlo a la solicitud durante todo su procesamiento
    - Incluir el Correlation-ID en el contexto para logs y para propagación saliente
    - _Requirements: 10.1, 2.6_

  - [ ]* 2.2 Escribir property test para la generación de Correlation-ID
    - **Property 3: Siempre existe un Correlation-ID UUID v4 cuando no se provee**
    - Hypothesis, mínimo 100 iteraciones, etiqueta `Feature: auth-2fa-poc, Property 3: Siempre existe un Correlation-ID UUID v4 cuando no se provee`
    - Given/When/Then: ausencia de header ⇒ UUID v4 válido en respuesta y logs
    - **Validates: Property 3 — Requirements 2.6, 10.1**

  - [x] 2.3 Implementar el logger estructurado con enmascarado de PII
    - Configurar `python-json-logger` para emitir registros estructurados que incluyan el Correlation-ID y excluyan tokens, contraseñas, códigos TOTP y PII (correo, documento)
    - Implementar la función pura `mask_sensitive(value)` que deja a lo sumo los últimos 4 caracteres del sufijo en claro
    - _Requirements: 10.3, 6.4_

  - [ ]* 2.4 Escribir property test para el enmascarado de PII
    - **Property 7: El enmascarado deja a lo sumo los últimos 4 caracteres en claro**
    - Hypothesis, mínimo 100 iteraciones, etiqueta `Feature: auth-2fa-poc, Property 7: El enmascarado deja a lo sumo los últimos 4 caracteres en claro`
    - Invariante: `len(unmasked) <= min(4, len(value))` y el no enmascarado es sufijo del original
    - **Validates: Property 7 — Requirements 6.4, 10.3**

  - [x] 2.5 Implementar `ErrorHandler` global
    - Capturar excepciones no controladas, responder un `ErrorResponse` genérico sin trazas ni detalles de implementación, registrar el detalle con el Correlation-ID y preservar el estado sin cambios parciales
    - _Requirements: 10.4, 10.7_

  - [x] 2.6 Implementar `StartupValidator` de variables de entorno
    - Al arrancar, validar que todas las variables de entorno requeridas estén presentes y no vacías; si falta alguna, abortar el arranque indicando los nombres faltantes sin exponer valores
    - _Requirements: 9.4_

  - [ ]* 2.7 Escribir property test para la validación de arranque
    - **Property 13: El arranque aborta si falta alguna variable de entorno requerida**
    - Hypothesis sobre subconjuntos de variables requeridas, mínimo 100 iteraciones, etiqueta `Feature: auth-2fa-poc, Property 13: El arranque aborta si falta alguna variable de entorno requerida`
    - Arranca si y solo si todas presentes y no vacías; si falta alguna, aborta indicando los nombres exactos y ningún secreto
    - **Validates: Property 13 — Requirements 9.4**

- [x] 3. Implementar la decisión de enrutamiento (`POST /api/v1/auth/route`)
  - [x] 3.1 Implementar la función pura `classify_route(identifier)`
    - Clasificar correo válido (RFC 5322, longitud 6–254) como `CLIENTE` con `login_method="password_totp"`; cadena de solo dígitos de longitud 5–15 como `INTERNO` con `login_method="saml_redirect"`
    - Clasificación determinista: misma entrada ⇒ misma ruta
    - _Requirements: 2.1, 2.2_

  - [ ]* 3.2 Escribir property test para la clasificación de ruta
    - **Property 1: La clasificación de ruta es determinista y correcta según el formato**
    - Hypothesis con generadores de correos válidos y cadenas numéricas, mínimo 100 iteraciones, etiqueta `Feature: auth-2fa-poc, Property 1: La clasificación de ruta es determinista y correcta según el formato`
    - **Validates: Property 1 — Requirements 2.1, 2.2**

  - [x] 3.3 Implementar el handler de `POST /api/v1/auth/route`
    - Validar server-side (tipo, longitud 5–254, formato) con `RouteRequest`; resolver ruta y devolver `RouteResponse`
    - Rechazar entradas fuera de especificación con `ErrorResponse` genérico (400/422) sin revelar motivo ni valor; registrar el rechazo con Correlation-ID sin el identificador
    - Registrar la decisión de ruta asociada al Correlation-ID, excluyendo el valor del identificador
    - _Requirements: 2.3, 2.4, 2.5, 1.3, 1.4, 10.6, 10.7_

  - [ ]* 3.4 Escribir property test para el rechazo genérico de entradas inválidas
    - **Property 2: La validación rechaza de forma genérica toda entrada fuera de especificación**
    - Hypothesis con entradas vacías/espacios/>254/formato inválido y TOTP no de 6 dígitos y UUID mal formados, mínimo 100 iteraciones, etiqueta `Feature: auth-2fa-poc, Property 2: La validación rechaza de forma genérica toda entrada fuera de especificación`
    - Invariante: nunca produce `route_type`, error genérico sin regla ni valor
    - **Validates: Property 2 — Requirements 2.3, 2.4, 1.3, 1.4, 10.6, 10.7**

  - [ ]* 3.5 Escribir property test para la no fuga de datos en respuestas y logs
    - **Property 8: No se filtran identificador, secretos ni PII en respuestas ni logs**
    - Hypothesis con logger capturado, mínimo 100 iteraciones, etiqueta `Feature: auth-2fa-poc, Property 8: No se filtran identificador, secretos ni PII en respuestas ni logs`
    - Invariante: identificador/secretos/PII sin enmascarar ∉ cuerpo, headers ni logs
    - **Validates: Property 8 — Requirements 2.5, 5.2, 10.3, 10.4**

- [x] 4. Checkpoint — Verificar enrutamiento y transversales
  - Ejecutar todas las pruebas (unitarias y de propiedad) y asegurar que pasan; preguntar al usuario si surgen dudas.

- [x] 5. Implementar la validación de transporte HTTPS y el cliente saliente
  - [x] 5.1 Implementar el validador de esquema de URL de proveedores
    - Rechazar toda URL cuyo esquema sea distinto de `https` sin transmitir datos y registrar el evento con el Correlation-ID; solo URLs `https` proceden
    - _Requirements: 10.5_

  - [ ]* 5.2 Escribir property test para la exigencia de HTTPS
    - **Property 10: Toda comunicación con proveedores exige HTTPS**
    - Hypothesis con esquemas variados, mínimo 100 iteraciones, etiqueta `Feature: auth-2fa-poc, Property 10: Toda comunicación con proveedores exige HTTPS`
    - **Validates: Property 10 — Requirements 10.5**

  - [x] 5.3 Implementar el cliente HTTP saliente con propagación de Correlation-ID y timeout
    - Propagar el Correlation-ID entrante en toda llamada saliente a proveedores; aplicar timeout de 15 s (fail-fast)
    - _Requirements: 10.2, 5.1_

  - [ ]* 5.4 Escribir property test para la propagación del Correlation-ID
    - **Property 11: El Correlation-ID se propaga en las llamadas salientes**
    - Hypothesis con cliente saliente mockeado, mínimo 100 iteraciones, etiqueta `Feature: auth-2fa-poc, Property 11: El Correlation-ID se propaga en las llamadas salientes`
    - Invariante: header saliente == Correlation-ID entrante
    - **Validates: Property 11 — Requirements 10.2**

- [x] 6. Implementar la creación de sesión (`POST /api/v1/auth/session`)
  - [x] 6.1 Implementar la verificación del ID token y la derivación de tipo de usuario
    - Integrar `firebase-admin`; verificar el ID token con `auth.verify_id_token` (firma, emisor, audiencia, vigencia, tolerancia de reloj ≤ 60 s)
    - Derivar `UserType` desde `firebase.sign_in_provider` (`password` → Cliente; `saml.<id>` → Interno)
    - _Requirements: 5.3, 5.5, 4.6, 4.7_

  - [x] 6.2 Implementar el handler de `POST /api/v1/auth/session` con creación de session cookie
    - Si la verificación falla, responder 401 genérico sin crear sesión; si tiene éxito, crear la cookie con `auth.create_session_cookie`
    - Entregar la session cookie con atributos `Secure`, `HttpOnly`, `SameSite=Strict`, `Path=/` y expiración acotada; no devolver tokens en el cuerpo ni usar localStorage
    - Mantener los secretos del Admin SDK exclusivamente server-side (nunca en respuestas, logs ni errores)
    - _Requirements: 5.1, 5.2, 5.4, 5.6, 4.8_

  - [ ]* 6.3 Escribir property test para "sesión solo con token verificado"
    - **Property 4: Nunca se establece sesión sin un token verificado**
    - Hypothesis + mock que lanza/retorna, mínimo 100 iteraciones, etiqueta `Feature: auth-2fa-poc, Property 4: Nunca se establece sesión sin un token verificado`
    - Implicación: verificación fallida ⇒ 401 y sin cookie; éxito ⇒ sesión creada
    - **Validates: Property 4 — Requirements 5.3, 5.5, 4.7**

  - [ ]* 6.4 Escribir property test para la cookie segura
    - **Property 5: Toda sesión creada viaja en cookie segura y nunca en el cuerpo**
    - Hypothesis, mínimo 100 iteraciones, etiqueta `Feature: auth-2fa-poc, Property 5: Toda sesión creada viaja en cookie segura y nunca en el cuerpo`
    - Aserción sobre atributos Set-Cookie (`Secure`/`HttpOnly`/`SameSite=Strict`, expiración acotada) y ausencia de token/cookie en el cuerpo
    - **Validates: Property 5 — Requirements 5.6**

- [x] 7. Implementar el recurso protegido (`GET /api/v1/dashboard`)
  - [x] 7.1 Implementar la validación de sesión y el armado de la respuesta del dashboard
    - Validar la session cookie con `auth.verify_session_cookie(cookie, check_revoked=True)`; si falla, 401 genérico sin incluir datos del dashboard y sin modificar la sesión
    - Con sesión válida, devolver `display_name`, `user_type` y `claims` enmascarados (allowlist de campos, enmascarado de correo/documento a ≤ 4 caracteres); si no hay claims, devolver `claims` vacío
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.6_

  - [ ]* 7.2 Escribir property test para la autorización del dashboard
    - **Property 6: El dashboard exige sesión válida y una respuesta no autorizada nunca filtra datos**
    - Hypothesis con mock de `verify_session_cookie`, mínimo 100 iteraciones, etiqueta `Feature: auth-2fa-poc, Property 6: El dashboard exige sesión válida y una respuesta no autorizada nunca filtra datos`
    - Invariante: fallo ⇒ 401 sin claims ni nombre; éxito ⇒ datos presentes
    - **Validates: Property 6 — Requirements 6.2, 6.3**

- [x] 8. Implementar el cierre de sesión (`POST /api/v1/auth/logout`)
  - [x] 8.1 Implementar el handler de logout idempotente
    - Revocar los tokens de refresco con `auth.revoke_refresh_tokens(uid)` y borrar la cookie (`Set-Cookie` con `Max-Age=0`); múltiples llamadas producen el mismo estado "sin sesión"
    - _Requirements: 6.5_

  - [ ]* 8.2 Escribir property test para la idempotencia del logout
    - **Property 9: El cierre de sesión es idempotente**
    - Hypothesis sobre N invocaciones, mínimo 100 iteraciones, etiqueta `Feature: auth-2fa-poc, Property 9: El cierre de sesión es idempotente`
    - Invariante: tras N≥1 logouts, cookie eliminada y tokens revocados; toda solicitud posterior es no autorizada
    - **Validates: Property 9 — Requirements 6.5**

  - [x] 8.3 Implementar el refuerzo de rate limiting en los endpoints del backend
    - Añadir rate limiting (estado efímero en memoria) en `/api/v1/auth/route` y en la creación de sesión como refuerzo de los bloqueos de Firebase (R3.7) y F5 (R4.10)
    - _Requirements: 3.7, 4.10_

- [x] 9. Checkpoint — Verificar backend completo
  - Ejecutar todas las pruebas del backend (unitarias y de propiedad) y asegurar ≥ 80% de cobertura en lógica de negocio; preguntar al usuario si surgen dudas.

- [x] 10. Implementar el Frontend_Angular (ingreso identifier-first y 2FA)
  - [x] 10.1 Implementar `AuthService` (orquestación del Client SDK y backend)
    - Inicializar el Firebase Client SDK; orquestar `signInWithEmailAndPassword`, `getMultiFactorResolver`, `TotpMultiFactorGenerator`, `SAMLAuthProvider`, `signInWithRedirect`, `getRedirectResult`
    - Llamar al backend `/api/v1/auth/route`, `/api/v1/auth/session`, `/api/v1/dashboard`, `/api/v1/auth/logout` vía `HttpClient` con credenciales de cookie
    - Aplicar `timeout(15000)` con RxJS; ante timeout/error de enrutamiento conservar el identificador y permitir reintento
    - Si no hay URL de backend resuelta por entorno, impedir llamadas y exponer error de configuración (sin default embebido)
    - _Requirements: 1.5, 1.6, 9.6, 9.7, 3.1_

  - [ ]* 10.2 Escribir pruebas unitarias de `AuthService` (Jest)
    - Given/When/Then con Firebase Client SDK y `HttpClient` mockeados: timeout de 15 s con reintento conservando identificador (R1.5, R1.6) y bloqueo de llamadas sin URL de backend (R9.7)
    - _Requirements: 1.5, 1.6, 9.7_

  - [x] 10.3 Implementar `LoginComponent` (identifier-first)
    - Único campo editable sin selector de tipo ni campos extra; validar no vacío tras `trim` y longitud ≤ 254 antes de enviar; transmitir el identificador normalizado al backend
    - Mostrar mensajes de validación/error y conservar el identificador ante error del backend
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.6_

  - [ ]* 10.4 Escribir pruebas unitarias de `LoginComponent` (Jest)
    - Given/When/Then: campo vacío/solo espacios ⇒ mensaje y no envía (R1.3); longitud > 254 ⇒ mensaje y no envía (R1.4)
    - _Requirements: 1.3, 1.4_

  - [x] 10.5 Implementar `TwoFactorComponent` (prompt TOTP)
    - Prompt de código TOTP de 6 dígitos para Cliente usando el MFA resolver (`TotpMultiFactorGenerator.assertionForSignIn`); para Interno manejar el retorno de `getRedirectResult` tras el redirect de F5
    - Mensajes de error genéricos sin revelar qué factor falló
    - _Requirements: 3.2, 4.3, 3.4, 3.5_

  - [ ]* 10.6 Escribir pruebas unitarias de `TwoFactorComponent` (Jest)
    - Given/When/Then con Client SDK mockeado: resolución MFA de Cliente y manejo de `getRedirectResult` de Interno; errores genéricos
    - _Requirements: 3.2, 4.3_

- [x] 11. Implementar el dashboard del frontend y la protección de ruta
  - [x] 11.1 Implementar `AuthGuard`
    - Proteger la ruta `dashboard` verificando la sesión consultando `/api/v1/dashboard` (fuente de verdad server-side); redirigir a `login` si no autorizado
    - _Requirements: 6.2, 6.3_

  - [x] 11.2 Implementar `DashboardComponent`
    - Mostrar nombre, tipo de usuario (`Cliente`/`Interno`) y la lista de claims enmascarados; render en ≤ 3 s tras la respuesta del backend
    - Si no hay claims, mostrar mensaje de ausencia de datos y no mostrar campos vacíos sin indicación
    - Implementar el cierre de sesión que invoca `/api/v1/auth/logout` y redirige a `login` en ≤ 3 s
    - _Requirements: 6.1, 6.5, 6.6_

  - [ ]* 11.3 Escribir pruebas unitarias de `DashboardComponent` (Jest)
    - Given/When/Then con `HttpClient` mockeado: dashboard sin claims muestra mensaje de ausencia (R6.6); logout redirige a login
    - _Requirements: 6.5, 6.6_

- [x] 12. Checkpoint — Verificar frontend completo
  - Ejecutar todas las pruebas del frontend (Jest) y asegurar que pasan; preguntar al usuario si surgen dudas.

- [x] 13. Implementar la CLI_Gestion (administración de usuarios Cliente)
  - [x] 13.1 Implementar la carga de credenciales y la validación estricta de parámetros
    - Cargar credenciales desde `GOOGLE_APPLICATION_CREDENTIALS` o archivo fuera de VCS; si faltan o son inválidas, abortar sin alterar datos y sin exponer el valor
    - Validar parámetros con parseo estricto (tipo, formato, presencia, claves no esperadas); ante parámetros inválidos/incompletos/claves no esperadas, abortar e identificar el parámetro problemático sin invocar las APIs de modificación
    - _Requirements: 7.7, 7.8, 7.9_

  - [ ]* 13.2 Escribir property test para "la CLI nunca altera datos ante entradas inválidas"
    - **Property 12: La CLI nunca altera datos ante entradas inválidas**
    - Hypothesis con parámetros inválidos y mock del Admin SDK, mínimo 100 iteraciones, etiqueta `Feature: auth-2fa-poc, Property 12: La CLI nunca altera datos ante entradas inválidas`
    - Invariante: `create_user`/`update_user`/`delete_user` no invocados; se identifica el parámetro inválido/faltante
    - **Validates: Property 12 — Requirements 7.7, 7.1**

  - [x] 13.3 Implementar el comando `crear_usuario`
    - Validar formato de correo (RFC 5322, 5–254); crear con `auth.create_user(email=...)`; si el correo ya existe (`EmailAlreadyExistsError`), abortar e informar duplicidad; retornar `uid` + indicador de éxito
    - _Requirements: 7.1, 7.2_

  - [x] 13.4 Implementar los comandos `actualizar_usuario` y `eliminar_usuario`
    - `actualizar_usuario --uid`: `auth.update_user(...)`; si el `uid` no existe (`UserNotFoundError`), abortar e informar no encontrado; retornar éxito
    - `eliminar_usuario --uid`: `auth.delete_user(uid)`; si el `uid` no existe, abortar e informar no encontrado; retornar éxito
    - _Requirements: 7.3, 7.4, 7.5_

  - [ ]* 13.5 Escribir pruebas unitarias de los comandos de la CLI (pytest)
    - Given/When/Then con Admin SDK mockeado: alta exitosa, duplicidad de correo, actualización/baja de usuario inexistente, ausencia/invalidez de credenciales
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.9_

- [ ] 14. Pruebas de integración contra proveedores reales (sin stubs)
  - [ ]* 14.1 Escribir pruebas de integración del flujo Cliente contra Firebase real
    - 1–3 ejemplos (no property-based): TOTP válido, TOTP inválido y bloqueo tras 5 intentos; verificación de ID token y creación de session cookie
    - Nota: el usuario aprovisionará las credenciales de Firebase y un usuario Cliente de prueba cuando se llegue a este paso
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.7, 9.1_

  - [ ]* 14.2 Escribir pruebas de integración del flujo Interno SAML (requiere acceso real a F5 APM / OpenText)
    - 1–3 ejemplos (no property-based): AuthnRequest SAML, validación de respuesta firmada y vigente, código 2FA corporativo válido/ inválido/ bloqueo tras 3 intentos
    - Nota: el usuario aprovisionará endpoints/credenciales de F5 APM y OpenText IDM cuando se llegue a este paso; marcado opcional por depender de infra corporativa real
    - _Requirements: 4.1, 4.2, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10, 9.1_

- [x] 15. Dockerización y configuración de despliegue
  - [x] 15.1 Crear la imagen Docker única del Backend_FastAPI
    - `Dockerfile` que produce una imagen idéntica en digest apta para local y Cloud Run sin reconstrucción; toda la configuración y secretos desde variables de entorno; soporte de graceful shutdown
    - Documentar ejecución local con `docker run`/`uvicorn` y `.env` fuera de VCS
    - _Requirements: 9.2, 9.3_

  - [x] 15.2 Configurar el despliegue del frontend y la resolución de URL por entorno
    - Configurar el build de Angular para Firebase Hosting; `environment.prod.ts` resuelve la URL del backend por entorno sin default embebido; headers de seguridad (HSTS, CSP, `X-Content-Type-Options: nosniff`, `X-Frame-Options`, `Referrer-Policy`) en el hosting
    - _Requirements: 9.5, 9.6, 9.7_

  - [ ]* 15.3 Preparar la configuración de despliegue en GCP (requiere sandbox real)
    - Manifiestos/variables para Cloud Run (backend) y Firebase Hosting (frontend); documentar la opción Memorystore (Redis) para el contador de rate limiting compartido entre instancias
    - Nota: marcado opcional por requerir el proyecto/sandbox GCP real que el usuario aprovisionará
    - _Requirements: 9.2, 9.3, 9.5_

- [x] 16. Elaborar la Guía Operativa (R8) — entregable documental
  - [x] 16.1 Redactar la Guía Operativa paso a paso
    - Pasos numerados y secuenciales, con ruta de navegación en consola y resultado observable esperado por paso, para: (a) verificar/habilitar Google Cloud Identity Platform (edición de pago, MFA TOTP + SAML) solo por consola de GCP sin gcloud CLI; (b) crear/administrar usuarios Cliente vía CLI_Gestion y consola de Firebase; (c) configurar la federación SAML Firebase (SP) ↔ F5 APM (IdP) con los parámetros en cada extremo; (d) generar el OTP en Google Authenticator desde el QR de Firebase (Cliente) y desde el QR de F5 APM (Interno)
    - Indicar la ubicación exacta y el tipo de valor para cada aprovisionamiento de credenciales/endpoints (F5 APM, Firebase, GCP), y para cada paso bloqueado la condición y la acción correctiva antes de reintentar
    - Nota: el usuario aprovisionará credenciales/endpoints (APM, Firebase, proyecto GCP) cuando se alcancen esos pasos
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_

- [x] 17. Integración final y actualización del CHANGELOG
  - [x] 17.1 Cablear los componentes y actualizar el CHANGELOG
    - Verificar el flujo extremo a extremo entre `AuthService` ↔ endpoints del backend ↔ Firebase Admin SDK; asegurar que no queda código huérfano sin integrar
    - Actualizar `CHANGELOG.md` (sección `[No publicado]`, categoría `Agregado`) con las funcionalidades implementadas de la PoC
    - _Requirements: 9.1, 9.3_

- [x] 18. Checkpoint final — Verificar todo
  - Ejecutar todas las pruebas (backend, frontend, CLI) y asegurar que pasan con ≥ 80% de cobertura en lógica de negocio; preguntar al usuario si surgen dudas.

## Notes

- Las tareas marcadas con `*` son opcionales (pruebas, pasos que requieren infraestructura corporativa/GCP real) y pueden omitirse para un MVP local más rápido.
- Cada tarea referencia los requisitos que satisface y, cuando aplica, la propiedad de corrección que valida (traza completa a las 13 propiedades).
- Las pruebas de propiedad usan Hypothesis con mínimo 100 iteraciones y la etiqueta `Feature: auth-2fa-poc, Property {n}: {texto}`.
- Las dependencias externas (Firebase Admin SDK, clientes HTTP) se mockean en pruebas unitarias; nunca se consumen backends reales en tests unitarios.
- Los pasos que requieren credenciales/endpoints reales (Firebase, F5 APM, OpenText IDM, proyecto GCP) serán aprovisionados por el usuario cuando el flujo lo requiera.
- Dependencias fijadas a versión exacta desde JFrog; sin lógica de negocio en el frontend; autorización server-side; logs estructurados con Correlation-ID y PII enmascarada.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.3", "1.4"] },
    { "id": 1, "tasks": ["1.2", "2.1", "2.3", "2.6"] },
    { "id": 2, "tasks": ["2.2", "2.4", "2.5", "2.7", "3.1", "5.1", "5.3", "13.1"] },
    { "id": 3, "tasks": ["3.2", "3.3", "5.2", "5.4", "6.1", "13.2", "13.3"] },
    { "id": 4, "tasks": ["3.4", "3.5", "6.2", "7.1", "8.1", "13.4"] },
    { "id": 5, "tasks": ["6.3", "6.4", "7.2", "8.2", "8.3", "10.1", "13.5"] },
    { "id": 6, "tasks": ["10.2", "10.3", "10.5", "11.1", "14.1", "14.2"] },
    { "id": 7, "tasks": ["10.4", "10.6", "11.2", "15.1", "15.2", "16.1"] },
    { "id": 8, "tasks": ["11.3", "15.3", "17.1"] }
  ]
}
```
