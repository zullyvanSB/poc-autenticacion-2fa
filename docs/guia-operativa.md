# Guía Operativa — PoC de Autenticación y 2FA (TOTP)

Entregable documental de la PoC (Requisito 8). Esta guía está dirigida a un
**operador con acceso a la consola de Google Cloud / Firebase pero sin la
`gcloud` CLI**. Todos los pasos de GCP se realizan exclusivamente por consola
web.

Cada paso indica:

- La **ruta de navegación** exacta en la consola o herramienta.
- El **resultado observable esperado** que confirma su finalización.
- Cuando aplica, el **tipo de valor** a introducir y **dónde** aprovisionarlo.
- Cuando un paso puede bloquearse, la **condición de bloqueo** y la **acción
  correctiva** antes de reintentar (R8.7).

> Nota de aprovisionamiento (transversal a toda la guía): esta PoC no simula ni
> mockea ningún componente. **El operador debe aportar las credenciales y
> endpoints reales** (ID del proyecto GCP, cuenta de servicio de Firebase,
> metadatos del IdP F5 APM, etc.) cuando el paso lo requiera. Los valores que
> aparecen entre `<...>` son marcadores de posición que el operador reemplaza
> por los valores reales de su entorno. Ningún secreto debe quedar escrito en el
> código, en la imagen Docker ni en el control de versiones.

---

## Convenciones y marcadores de posición

| Marcador | Significado | Dónde se obtiene |
|---|---|---|
| `<PROJECT_ID>` | ID del proyecto de GCP de la PoC | Consola de GCP → selector de proyecto |
| `<TENANT_ID>` | ID del tenant dedicado de Identity Platform que aísla la PoC (p. ej. `poc-auth-2fa-vh5h3`) | Lo genera el operador al crear el tenant (Sección 1) |
| `<APP_DOMAIN>` | Dominio del Frontend_Angular (p. ej. `localhost` en local, `<PROJECT_ID>.web.app` en GCP) | Entorno de despliegue |
| `<SP_ENTITY_ID>` | Entity ID del Service Provider (nuestra app) | Lo define el operador (típicamente la URL de la app) |
| `<APM_ENTITY_ID>` | Entity ID del IdP (F5 APM) | Metadatos SAML de F5 APM |
| `<APM_SSO_URL>` | URL de SSO del IdP (F5 APM) | Metadatos SAML de F5 APM |
| `<APM_CERT>` | Certificado de firma de tokens del IdP (F5 APM), en formato PEM | Metadatos SAML / administrador de F5 APM |
| `<SAML_PROVIDER_ID>` | ID del proveedor SAML en Identity Platform; **debe comenzar con `saml.`** (p. ej. `saml.f5apm`) | Lo define el operador al crear el proveedor |

---

## Sección 0 — Prerrequisitos (verificación previa)

Antes de ejecutar la guía, verifique:

1. Tener un **proyecto de GCP** (`<PROJECT_ID>`) con un rol que permita
   administrar Identity Platform (p. ej. `roles/identityplatform.admin` o un rol
   con los permisos equivalentes). La selección de proyecto no requiere un rol
   específico; crear uno requiere `roles/resourcemanager.projectCreator`.
   **Administrar tenants** (crear el tenant dedicado, habilitar multi-tenancy y
   configurar su MFA) también requiere un rol de administrador de Identity
   Platform (p. ej. `roles/identityplatform.admin`); sin él, las llamadas de
   administración de tenants devuelven `403`/permiso denegado.
   - **Nota (proyecto compartido):** cuando el proyecto de GCP aloja otras
     aplicaciones y sus usuarios reales, esta guía **aísla la PoC en un tenant
     dedicado** de Identity Platform para no mezclar usuarios ni configuración de
     MFA/SAML con lo existente (ver el nuevo **Paso 1.6**). Este fue el caso real
     de esta PoC.
   - **Resultado observable:** al abrir la consola de GCP y usar el selector de
     proyecto, `<PROJECT_ID>` aparece y queda seleccionado en la barra superior.
   - **Bloqueo (R8.7):** si no ve el proyecto o la consola indica permisos
     insuficientes. **Acción correctiva:** solicitar al administrador de la
     organización que le conceda acceso al proyecto con el rol adecuado antes de
     reintentar.

2. Tener la **facturación habilitada** en el proyecto. Identity Platform en su
   **edición de pago** (la que soporta MFA TOTP y federación SAML) requiere
   facturación activa.
   - **Ruta:** Consola de GCP → menú de navegación (☰) → **Facturación**.
   - **Resultado observable:** el proyecto muestra una cuenta de facturación
     vinculada con estado activo.
   - **Bloqueo (R8.7):** facturación no vinculada o inactiva. **Acción
     correctiva:** vincular una cuenta de facturación válida (o solicitarlo al
     administrador) antes de habilitar la edición de pago.

3. Tener instalados y configurados los proyectos locales de la PoC
   (`backend/`, `frontend/`, `cli/`) según sus respectivos README, con las
   dependencias resueltas desde JFrog.

---

## Sección 1 — Verificar y habilitar Google Cloud Identity Platform (solo consola, sin `gcloud`)

Objetivo (R8.1): habilitar la edición de Identity Platform que soporta **MFA
TOTP** y **federación SAML**, confirmando cada paso con un resultado observable.

### Paso 1.1 — Abrir la consola y seleccionar el proyecto

- **Ruta:** Navegador → `https://console.cloud.google.com` → selector de
  proyecto (barra superior) → seleccionar `<PROJECT_ID>`.
- **Resultado observable:** el nombre `<PROJECT_ID>` aparece en la barra
  superior de la consola.
- **Bloqueo (R8.7):** no aparece el proyecto. **Acción correctiva:** verificar
  que inició sesión con la cuenta correcta y que tiene acceso al proyecto (ver
  Sección 0.1).

### Paso 1.2 — Verificar si Identity Platform ya está habilitado

- **Ruta:** menú de navegación (☰) → en el buscador de productos de la consola,
  escribir **"Identity Platform"** → abrir la página de **Identity Platform**.
- **Resultado observable (ya habilitado):** se abre el panel de Identity
  Platform mostrando las secciones **Providers / Users / Settings**. En ese caso
  continúe en la Sección 1 a partir del Paso 1.4 (configuración de TOTP).
- **Resultado observable (no habilitado):** la consola muestra una página de
  **habilitación** (botón **"Enable Identity Platform"** o la ficha del producto
  en Marketplace). Continúe con el Paso 1.3.

### Paso 1.3 — Habilitar Identity Platform (edición de pago)

- **Ruta:** página de Identity Platform → botón **"Enable Identity Platform"**
  (si la consola lo redirige a Marketplace, use **"Habilitar" / "Enable"** en la
  ficha del producto **Identity Platform**).
- **Resultado observable:** tras habilitar, la consola redirige al panel de
  Identity Platform con las secciones **Providers**, **Users** y **Settings**
  disponibles.
- **Aprovisionamiento (R8.6):** no requiere credenciales manuales aquí; la
  habilitación se asocia a la facturación del proyecto. La **cuenta de servicio
  de Firebase** (usada por el backend y la CLI) se gestiona por separado (ver
  Sección 2 y Sección 6).
- **Bloqueo (R8.7):** la habilitación falla indicando que la facturación no está
  activa. **Acción correctiva:** completar la Sección 0.2 (habilitar
  facturación) y reintentar. Si falla por permisos, solicitar el rol de
  administrador de Identity Platform.

### Paso 1.4 — Verificar/activar el proveedor de correo (base para Clientes)

Los Clientes se autentican con correo electrónico; el proveedor **Email/Password**
debe estar activo para que el 2FA TOTP tenga sentido.

- **Ruta:** Identity Platform → **Providers** → **Add a Provider** → seleccionar
  **Email / Password** → **Enable** → **Save**. Si ya aparece en la lista de
  providers, no hace falta volver a añadirlo.
- **Resultado observable:** en **Providers**, **Email/Password** figura con
  estado **Enabled**.

### Paso 1.5 — Habilitar la autenticación multifactor TOTP (R8.1)

> **Recomendación (proyecto compartido):** si el proyecto `<PROJECT_ID>` se
> comparte con otras aplicaciones y usuarios reales, el enfoque recomendado es
> **habilitar el TOTP a nivel de TENANT (ver Paso 1.6), NO a nivel de proyecto**.
> Habilitar MFA a nivel de proyecto afecta a toda la configuración de Identity
> Platform del proyecto. Si durante la exploración se habilitó el TOTP a nivel de
> proyecto, **revertirlo a `DISABLED`** antes de continuar con el enfoque por
> tenant, ejecutando desde Cloud Shell:
>
> ```bash
> curl -X PATCH "https://identitytoolkit.googleapis.com/admin/v2/projects/<PROJECT_ID>/config?updateMask=mfa.state" \
>     -H "Authorization: Bearer $(gcloud auth print-access-token)" \
>     -H "Content-Type: application/json" \
>     -H "X-Goog-User-Project: <PROJECT_ID>" \
>     -d '{ "mfa": { "state": "DISABLED" } }'
> ```
>
> **Nota (verificada):** el `mfa` de nivel de proyecto tiene dos campos `state`
> independientes; fijar el `mfa.state` de nivel superior en `DISABLED` basta para
> desactivarlo aunque permanezca un `totpProviderConfig` en `providerConfigs`.

> **Importante (corrección verificada):** a diferencia del MFA por SMS, el **TOTP MFA NO se habilita desde la consola**. En la pantalla **Identity Platform → MFA** solo aparece **"SMS based Multi-Factor Authentication"**; no existe un interruptor de TOTP en la consola. Esto es esperado, no un error. Según la documentación oficial de Google Cloud, el TOTP MFA se habilita a nivel de proyecto **únicamente mediante el Admin SDK o la REST API de configuración** de Identity Platform.
>
> El contenido que sigue describe el enfoque **a nivel de proyecto**. En esta PoC
> (proyecto compartido) el enfoque efectivamente adoptado fue el **a nivel de
> tenant** del Paso 1.6; se conserva esta descripción como referencia para
> proyectos dedicados.

- **Ruta:** abrir **Cloud Shell** (ícono `>_` en la barra superior de la consola de GCP; trae `gcloud` preautenticado, por lo que no requiere instalar `gcloud` localmente) y ejecutar la REST API de administración de Identity Platform.
- **Acción:** ejecutar el siguiente comando, reemplazando `<PROJECT_ID>` por el ID del proyecto (en esta PoC se usó `sandbox-arquitectura`):

```bash
curl -X PATCH "https://identitytoolkit.googleapis.com/admin/v2/projects/<PROJECT_ID>/config?updateMask=mfa" \
    -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    -H "Content-Type: application/json" \
    -H "X-Goog-User-Project: <PROJECT_ID>" \
    -d '{
      "mfa": {
        "state": "ENABLED",
        "providerConfigs": [{
          "state": "ENABLED",
          "totpProviderConfig": { "adjacentIntervals": 5 }
        }]
      }
    }'
```

  - `adjacentIntervals: 5` es la tolerancia de desfase de reloj por defecto (número de ventanas TOTP adyacentes de 30 s aceptadas); rango permitido 0–10.
- **Advertencia crítica (gotcha verificado):** el objeto `mfa` tiene **dos** campos `state` independientes: el `mfa.state` de nivel superior (interruptor maestro) y el `providerConfigs[].state` de cada proveedor. **Ambos deben quedar en `"ENABLED"`.** Si solo se habilita el `state` del proveedor, el `mfa.state` maestro permanece `"DISABLED"` y el TOTP no surte efecto. El comando anterior fija ambos.
- **Resultado observable:** la respuesta JSON de la API contiene un bloque `mfa` con `"state": "ENABLED"` tanto en el nivel superior como dentro del único `providerConfigs[].state`, junto con `totpProviderConfig.adjacentIntervals`:

```json
"mfa": {
  "state": "ENABLED",
  "providerConfigs": [
    { "totpProviderConfig": { "adjacentIntervals": 5 }, "state": "ENABLED" }
  ]
}
```

- **Prerrequisitos (doc oficial):** debe haber al menos un proveedor compatible con MFA habilitado (Email/Password, Paso 1.4 ✓); la app debe verificar el correo del usuario (el MFA exige verificación de correo); y el TOTP MFA requiere Web SDK v9.19.1+ (el frontend usa Firebase 12.x ✓).
- **Alternativa (Admin SDK):** también puede habilitarse con el Firebase Admin SDK (Node ≥ 11.6.0) mediante `getAuth().projectConfigManager().updateProjectConfig({ multiFactorConfig: { providerConfigs: [{ state: "ENABLED", totpProviderConfig: { adjacentIntervals: 5 } }] } })`. En esta PoC se usó la REST API desde Cloud Shell.
- **Aprovisionamiento (R8.6):** no requiere endpoints externos; es configuración del propio Identity Platform aplicada vía API.
- **Bloqueo (R8.7):** si la respuesta muestra `mfa.state` en `"DISABLED"`, reejecutar el comando incluyendo el `"state": "ENABLED"` de nivel superior. Si la API responde `403`/permiso denegado, el operador necesita un rol de administrador de Identity Platform. Nota: este cambio es a **nivel de proyecto** y afecta a toda la configuración de Identity Platform del proyecto (relevante si el proyecto se comparte con otras aplicaciones).

### Paso 1.6 — Aislar la PoC en un tenant dedicado (recomendado en proyecto compartido)

**Por qué (R8.1):** aislar la PoC en un **tenant** dedicado mantiene sus usuarios
y su configuración de MFA/SAML separados de los usuarios y las apps que ya
existen en el proyecto compartido. Al finalizar la PoC, el tenant puede
eliminarse sin dejar rastro ni afectar lo demás. En esta PoC esta fue la
situación real: el proyecto `<PROJECT_ID>` (p. ej. `sandbox-arquitectura`) se
comparte con otras aplicaciones y usuarios reales, por lo que todo el trabajo de
la PoC se confina a un tenant dedicado.

Todos los comandos de este paso se ejecutan desde **Cloud Shell** (ícono `>_` en
la barra superior de la consola de GCP; trae `gcloud` preautenticado).

#### 1.6.a — Habilitar multi-tenancy en el proyecto (una sola vez)

- **Ruta:** Cloud Shell.
- **Acción:** habilitar `multiTenant.allowTenants` en la configuración del
  proyecto (reemplazar `<PROJECT_ID>`, p. ej. `sandbox-arquitectura`):

```bash
curl -X PATCH "https://identitytoolkit.googleapis.com/admin/v2/projects/<PROJECT_ID>/config?updateMask=multiTenant.allowTenants" \
    -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    -H "Content-Type: application/json" \
    -H "X-Goog-User-Project: <PROJECT_ID>" \
    -d '{ "multiTenant": { "allowTenants": true } }'
```

- **Resultado observable:** la respuesta muestra `"multiTenant": { "allowTenants": true }`.
- **Nota de alcance:** habilitar multi-tenancy es un cambio a **nivel de
  proyecto**; las apps existentes y sus usuarios siguen en el nivel
  "default/proyecto" y **no se ven afectados**.

#### 1.6.b — Crear el tenant dedicado

- **Ruta:** Cloud Shell.
- **Acción:** crear el tenant de la PoC (reemplazar `<PROJECT_ID>`):

```bash
curl -X POST "https://identitytoolkit.googleapis.com/v2/projects/<PROJECT_ID>/tenants" \
    -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    -H "Content-Type: application/json" \
    -H "X-Goog-User-Project: <PROJECT_ID>" \
    -d '{ "displayName": "poc-auth-2fa", "allowPasswordSignup": true, "enableEmailLinkSignin": false }'
```

- **Resultado observable:** la respuesta trae
  `name: projects/<PROJECT_NUMBER>/tenants/<TENANT_ID>` (en esta PoC el
  `<TENANT_ID>` resultó `poc-auth-2fa-vh5h3`). Copiar ese `<TENANT_ID>` (el
  sufijo del `name`), que se usa en el resto de la guía y en la configuración de
  backend, frontend y CLI. `allowPasswordSignup: true` deja Email/Password activo
  dentro del tenant.

#### 1.6.c — Habilitar TOTP MFA en el TENANT (campo `mfaConfig`, no `mfa`)

- **Ruta:** Cloud Shell.
- **Acción:** habilitar el MFA TOTP del tenant (reemplazar `<PROJECT_ID>` y
  `<TENANT_ID>`, p. ej. `poc-auth-2fa-vh5h3`). Nótese que a nivel de tenant el
  campo se llama **`mfaConfig`** (no `mfa` como a nivel de proyecto):

```bash
curl -X PATCH "https://identitytoolkit.googleapis.com/v2/projects/<PROJECT_ID>/tenants/<TENANT_ID>?updateMask=mfaConfig" \
    -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    -H "Content-Type: application/json" \
    -H "X-Goog-User-Project: <PROJECT_ID>" \
    -d '{ "mfaConfig": { "state": "ENABLED", "providerConfigs": [{ "state": "ENABLED", "totpProviderConfig": { "adjacentIntervals": 5 } }] } }'
```

- **Resultado observable:** la respuesta del tenant incluye
  `mfaConfig.state: "ENABLED"` y el `totpProviderConfig`.

#### 1.6.d — Verificar el aislamiento (proyecto `DISABLED`, tenant `ENABLED`)

- **Ruta:** Cloud Shell.
- **Acción:** consultar la configuración del **proyecto** (su bloque `mfa` debe
  estar `DISABLED`):

```bash
curl -X GET "https://identitytoolkit.googleapis.com/admin/v2/projects/<PROJECT_ID>/config" \
    -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    -H "X-Goog-User-Project: <PROJECT_ID>"
```

  y consultar la configuración del **tenant** (su bloque `mfaConfig` debe estar
  `ENABLED`):

```bash
curl -X GET "https://identitytoolkit.googleapis.com/v2/projects/<PROJECT_ID>/tenants/<TENANT_ID>" \
    -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    -H "X-Goog-User-Project: <PROJECT_ID>"
```

- **Resultado observable esperado:**

  | Nivel | Campo | Estado esperado |
  |---|---|---|
  | Proyecto | `mfa.state` | `DISABLED` |
  | Tenant (`<TENANT_ID>`) | `mfaConfig.state` | `ENABLED` |

- **Bloqueo (R8.7):** si la respuesta del tenant muestra el TOTP sin habilitar,
  reejecutar el Paso 1.6.c; si el proyecto no está `DISABLED`, ejecutar el
  comando de reversión del Paso 1.5.

#### 1.6.e — Bloqueo (R8.7)

- Si la creación del tenant (`.../tenants`) devuelve `INVALID_PROJECT_ID`, la
  **multi-tenancy aún no está habilitada**. **Acción correctiva:** ejecutar el
  Paso 1.6.a (`allowTenants: true`) y reintentar.
- Si cualquier llamada responde `403`/permiso denegado, falta el rol de
  administrador de Identity Platform. **Acción correctiva:** solicitarlo al
  administrador antes de reintentar.

---

## Sección 2 — Crear y administrar usuarios Cliente (CLI_Gestion y consola)

Objetivo (R8.2): dar de alta y administrar usuarios Cliente, con entradas
requeridas y resultado observable por operación. La gestión está restringida a
la **CLI_Gestion** y a la **consola de Firebase**; el Backend_FastAPI no expone
endpoints administrativos (R7.6).

### Paso 2.0 — Aprovisionar las credenciales de la CLI (R8.6)

La CLI_Gestion usa el **Firebase Admin SDK** y necesita una **cuenta de
servicio** con permisos sobre Identity Platform/Firebase Authentication.

- **Dónde obtener el valor (ubicación exacta):**
  - **Consola de Firebase:** `https://console.firebase.google.com` →
    seleccionar el proyecto `<PROJECT_ID>` → ícono de engranaje
    (**Configuración del proyecto**) → pestaña **Cuentas de servicio** →
    **Generar nueva clave privada**. Se descarga un archivo JSON de cuenta de
    servicio.
  - Alternativamente, en la consola de GCP: **IAM y administración** →
    **Cuentas de servicio** → crear/usar una cuenta y generar una clave JSON.
- **Tipo de valor:** ruta de sistema de archivos a un archivo **JSON de cuenta
  de servicio**, almacenado **fuera del control de versiones** (R7.8).
- **Cómo lo consume la CLI** (precedencia): argumento `--credentials <ruta>`, o
  la variable de entorno `GOOGLE_APPLICATION_CREDENTIALS`.

```bash
# Ejemplo (no incluir el archivo en git):
export GOOGLE_APPLICATION_CREDENTIALS=/ruta/segura/service-account.json
```

- **Aprovisionar también el tenant (obligatorio):** además de las credenciales,
  la CLI **requiere** el tenant dedicado creado en el Paso 1.6. Indíquelo por la
  variable de entorno `FIREBASE_TENANT_ID=<TENANT_ID>` o por el argumento
  `--tenant <TENANT_ID>` (en esta PoC, `<TENANT_ID>` = `poc-auth-2fa-vh5h3`). Los
  usuarios creados por la CLI de esta forma viven **dentro del tenant**,
  separados de los usuarios a nivel de proyecto.

```bash
# Ejemplo (alternativa por variable de entorno; equivale a pasar --tenant):
export FIREBASE_TENANT_ID=poc-auth-2fa-vh5h3
```

- **Resultado observable:** al ejecutar cualquier comando de la CLI, no aparece
  el mensaje de credenciales ausentes/ inválidas ni el de tenant ausente.
- **Bloqueo (R8.7):** la CLI aborta con un mensaje de credenciales no
  encontradas o inválidas. **Acción correctiva:** verificar que la ruta exista y
  apunte a un archivo JSON de cuenta de servicio vigente; definir
  `GOOGLE_APPLICATION_CREDENTIALS` o pasar `--credentials`. El mensaje de error
  nunca revela el contenido del archivo (R7.9).
- **Bloqueo (R8.7) — tenant ausente:** si no se define `FIREBASE_TENANT_ID` ni se
  pasa `--tenant <TENANT_ID>`, la CLI **aborta antes de realizar cualquier
  cambio**. **Acción correctiva:** exportar `FIREBASE_TENANT_ID=<TENANT_ID>` o
  añadir `--tenant <TENANT_ID>` y reintentar.

### Paso 2.1 — Alta de un usuario Cliente por CLI (R8.2)

- **Entrada requerida:** correo electrónico con formato válido (RFC 5322,
  longitud 5–254).
- **Comando** (desde la carpeta `cli/`):

```bash
poetry run crear_usuario --email <cliente@ejemplo.com> --tenant <TENANT_ID>
```

  (puede omitir `--tenant` si definió `FIREBASE_TENANT_ID`, ver Paso 2.0.)

- **Resultado observable:** la CLI retorna el **UID** del usuario creado junto a
  un indicador de éxito. El usuario queda **dentro del tenant** `<TENANT_ID>`,
  separado de los usuarios a nivel de proyecto.
- **Bloqueo (R8.7):**
  - El correo ya existe → la CLI aborta e informa **duplicidad** (R7.2).
    **Acción correctiva:** usar un correo distinto o administrar el usuario
    existente con `actualizar_usuario`.
  - Correo con formato inválido o claves no esperadas → la CLI aborta e
    identifica el parámetro problemático (R7.7). **Acción correctiva:** corregir
    el parámetro y reintentar.

### Paso 2.2 — Actualización de un usuario Cliente por CLI (R8.2)

- **Entrada requerida:** `--uid` de un usuario existente; opcionalmente
  `--email` y/o `--display-name`.
- **Comando:**

```bash
poetry run actualizar_usuario --uid <uid> --display-name "<Nombre Nuevo>" --tenant <TENANT_ID>
```

  (puede omitir `--tenant` si definió `FIREBASE_TENANT_ID`, ver Paso 2.0.)

- **Resultado observable:** la CLI retorna un indicador de éxito.
- **Bloqueo (R8.7):** el `uid` no existe → la CLI aborta e informa **usuario no
  encontrado** (R7.5). **Acción correctiva:** verificar el `uid` en la consola
  de Firebase (Paso 2.5) y reintentar con el valor correcto.

### Paso 2.3 — Baja de un usuario Cliente por CLI (R8.2)

- **Entrada requerida:** `--uid` de un usuario existente.
- **Comando:**

```bash
poetry run eliminar_usuario --uid <uid> --tenant <TENANT_ID>
```

  (puede omitir `--tenant` si definió `FIREBASE_TENANT_ID`, ver Paso 2.0.)

- **Resultado observable:** la CLI retorna un indicador de éxito.
- **Bloqueo (R8.7):** el `uid` no existe → la CLI aborta e informa **usuario no
  encontrado** (R7.5). **Acción correctiva:** confirmar el `uid` en la consola
  de Firebase y reintentar.

### Paso 2.4 — Alta/administración por consola de Firebase (R8.2)

Alternativa a la CLI, útil para inspección visual.

- **Ruta:** Consola de Firebase → `<PROJECT_ID>` → **Authentication** →
  pestaña **Users** → botón **Add user** (correo). Para eliminar o editar, usar
  el menú de acciones (⋮) de cada fila.
- **Nota (tenant):** para ver y administrar los usuarios de la PoC debe
  **seleccionar el tenant `<TENANT_ID>`** en el selector de tenants de la consola
  de Identity Platform/Firebase. Los usuarios del tenant **no** aparecen en la
  lista default/proyecto.
- **Resultado observable:** el usuario aparece en la tabla **Users** del tenant
  seleccionado con su correo, su **User UID** y su fecha de creación.

### Paso 2.5 — Verificar un usuario (consola)

- **Ruta:** Consola de Firebase → **Authentication** → (seleccionar el tenant
  `<TENANT_ID>` en el selector de tenants) → **Users** → localizar el correo o el
  **User UID**.
- **Nota (tenant):** si no selecciona el tenant `<TENANT_ID>`, verá los usuarios
  del nivel default/proyecto y no encontrará los usuarios de la PoC.
- **Resultado observable:** la fila del usuario muestra el `UID` que la CLI usa
  en `actualizar_usuario`/`eliminar_usuario`. Este es el valor correcto para
  `--uid`.

---

## Sección 3 — Configurar la federación SAML: Firebase (SP) ↔ F5 APM (IdP)

Objetivo (R8.3): establecer la federación donde **Firebase/Identity Platform
actúa como Service Provider (SP)** y **F5 APM actúa como Identity Provider
(IdP)**, especificando los parámetros en **cada extremo** y el resultado
observable que confirma la federación.

> **Procedimiento listo para ejecutar:** los pasos concretos para dejar el
> flujo Interno operativo cuando lleguen los metadatos de F5 APM (crear el
> proveedor SAML en el tenant por consola o REST API, pegar el Provider ID en
> `backend/.env` y `frontend/src/config.js`, autorizar `localhost` y probar en
> local) están consolidados en `docs/runbook-flujo-interno.md`. Esta Sección 3
> describe el detalle conceptual de ambos extremos; el runbook es la checklist
> operativa. En esta PoC los valores del SP ya quedaron definidos:
> **ACS URL** `https://sandbox-arquitectura.firebaseapp.com/__/auth/handler` y
> **SP Entity ID** `https://sandbox-arquitectura.firebaseapp.com`.

> **Nota (tenant):** para el flujo **Interno**, el proveedor SAML debe crearse
> **dentro del tenant** dedicado. En la consola de Identity Platform, **seleccione
> primero el tenant `<TENANT_ID>`** (p. ej. `poc-auth-2fa-vh5h3`) y luego vaya a
> **Identity providers → Add a Provider → SAML**. El frontend establece
> `auth.tenantId = <TENANT_ID>` para que `signInWithRedirect` opere dentro del
> tenant. La **URL de callback no cambia**: sigue siendo
> `https://<PROJECT_ID>.firebaseapp.com/__/auth/handler`.

### Visión de los dos extremos

| Parámetro | Extremo donde se introduce | Valor / tipo de valor | Origen |
|---|---|---|---|
| Provider ID (`<SAML_PROVIDER_ID>`) | Identity Platform (SP) | Cadena que **debe empezar con `saml.`** (p. ej. `saml.f5apm`) | Lo define el operador |
| IdP **Entity ID** (`<APM_ENTITY_ID>`) | Identity Platform (SP) | Cadena/URI identificadora del IdP | Metadatos SAML de F5 APM |
| IdP **SSO URL** (`<APM_SSO_URL>`) | Identity Platform (SP) | URL **HTTPS** del endpoint SSO del IdP | Metadatos SAML de F5 APM |
| IdP **Certificado de firma** (`<APM_CERT>`) | Identity Platform (SP) | Certificado X.509 en **PEM** (incluye líneas `BEGIN/END CERTIFICATE`) | Metadatos SAML de F5 APM |
| SP **Entity ID** (`<SP_ENTITY_ID>`) | Identity Platform (SP) y configuración del SP en F5 APM (como *audience*) | Cadena/URI (típicamente la URL de la app) | Lo define el operador |
| **Authorization callback / ACS URL** | Configuración del SP en F5 APM | `https://<PROJECT_ID>.firebaseapp.com/__/auth/handler` | Lo genera Identity Platform |
| Dominio autorizado (`<APP_DOMAIN>`) | Identity Platform (SP) | Dominio de la app (p. ej. `<PROJECT_ID>.web.app`) | Entorno de despliegue |

### Paso 3.1 — Crear el proveedor SAML en Identity Platform (lado SP)

- **Ruta:** Consola de GCP → **Identity Platform** → **Identity providers** →
  **Add a Provider** → seleccionar **SAML** de la lista.
- **Resultado observable:** se abre el formulario de configuración del proveedor
  SAML.

### Paso 3.2 — Introducir los parámetros del IdP (F5 APM) en el SP (R8.3, R8.6)

En el formulario del proveedor SAML, introduzca los datos del **IdP (F5 APM)**:

1. **Name:** nombre descriptivo (p. ej. `F5 APM Corporativo`). Si usa un nombre
   personalizado, pulse **Edit** junto a **Provider ID** para fijar
   `<SAML_PROVIDER_ID>` (debe comenzar con `saml.`).
2. **Entity ID (del proveedor):** `<APM_ENTITY_ID>`.
3. **SAML SSO URL:** `<APM_SSO_URL>` (debe ser **HTTPS**).
4. **Certificate (token-signing):** `<APM_CERT>` en PEM, **incluyendo** las
   líneas `-----BEGIN CERTIFICATE-----` y `-----END CERTIFICATE-----`.

- **Dónde se aprovisionan estos valores (R8.6):** provienen de los **metadatos
  SAML del IdP F5 APM**; solicítelos al administrador de F5 APM o expórtelos
  desde la consola de F5 APM (metadata del Access Policy correspondiente).
- **Bloqueo (R8.7):** aún no dispone de los metadatos del IdP. **Acción
  correctiva:** solicitar al administrador de F5 APM el Entity ID, la SSO URL y
  el certificado de firma del Access Policy; no continúe hasta tenerlos, pues la
  federación no puede validarse sin ellos.

### Paso 3.3 — Declarar el Service Provider (nuestra app) en el SP

- En la sección **Service provider** del mismo formulario, introduzca el
  **Entity ID** de la app: `<SP_ENTITY_ID>` (típicamente la URL de la app). En
  el lado del IdP (F5 APM) este valor se conoce como **audience**.
- En **Project settings** (panel lateral), pulse **Add Domain** y añada
  `<APP_DOMAIN>` (p. ej. si la URL de inicio de sesión es
  `https://<APP_DOMAIN>/login`, añada `<APP_DOMAIN>`).
- **Copie** el valor del campo **Authorization callback (URL)**. Su forma por
  defecto es:

```
https://<PROJECT_ID>.firebaseapp.com/__/auth/handler
```

- Pulse **Save**.
- **Resultado observable:** el proveedor SAML aparece en **Identity providers**
  con su `<SAML_PROVIDER_ID>` y estado habilitado.

### Paso 3.4 — Configurar el Service Provider en F5 APM (lado IdP)

En la administración de **F5 APM**, registre a Firebase como SP del Access
Policy SAML:

- **ACS URL / Authorization callback:** pegue el valor copiado en el Paso 3.3
  (`https://<PROJECT_ID>.firebaseapp.com/__/auth/handler`). Es el endpoint al
  que el IdP redirige la **SAML Response**.
- **SP Entity ID / audience:** `<SP_ENTITY_ID>` (el mismo valor del Paso 3.3).
- Asegúrese de que el IdP emita en la aserción los elementos `<saml:Subject>` y
  `<saml:NameID>` requeridos por Identity Platform; si el correo del usuario debe
  propagarse, inclúyalo en el `NameID` con formato `emailAddress`.
- **Dónde se aprovisionan estos valores (R8.6):** en la configuración del Access
  Policy / objeto SP de la consola de F5 APM. Requiere un operador con permisos
  sobre F5 APM.
- **Bloqueo (R8.7):** no tiene acceso de administración a F5 APM. **Acción
  correctiva:** coordinar con el administrador corporativo de F5 APM el registro
  del SP con los valores anteriores antes de probar el flujo.

### Paso 3.5 — Verificar el establecimiento de la federación (R8.3)

- **Ruta de prueba:** desde el Frontend_Angular, iniciar sesión con un
  identificador que el Backend_FastAPI clasifique como **Interno** (número de
  identificación de 5–15 dígitos). El frontend ejecuta
  `signInWithRedirect(SAMLAuthProvider('<SAML_PROVIDER_ID>'))` y es redirigido a
  F5 APM.
- **Resultado observable (federación establecida):** el navegador es redirigido
  a la pantalla de inicio de sesión de **F5 APM** y, tras autenticarse y
  completar el 2FA corporativo, regresa a `<APP_DOMAIN>` con la sesión
  establecida (Firebase emite un ID token verificable por el backend).
- **Bloqueo (R8.7):**
  - Error de **firma inválida/expirada** de la aserción → el acceso se rechaza
    con error genérico (R4.7). **Acción correctiva:** confirmar que `<APM_CERT>`
    cargado en el SP corresponde al certificado de firma vigente del IdP;
    coordinar la rotación con el administrador de F5 APM si expiró.
  - Error de **audience/Entity ID no coincide** → verificar que
    `<SP_ENTITY_ID>` sea idéntico en ambos extremos (Paso 3.3 y 3.4).
  - Error de **dominio no autorizado** → añadir `<APP_DOMAIN>` en
    **Project settings → Add Domain** (Paso 3.3).

---

## Sección 4 — Generar el OTP (Cliente) desde el QR de Firebase

Objetivo (R8.4): un usuario que simula un **Cliente** enrola y genera el OTP en
**Google Authenticator** a partir del QR mostrado por el Frontend_Angular
durante el enrolamiento.

> **Aclaracion tecnica (verificada):** Google Cloud Identity Platform NO
> entrega una imagen de QR ya renderizada. Durante el enrolamiento, el Client
> SDK genera un **secreto TOTP** (`TotpMultiFactorGenerator.generateSecret`) y
> una URL `otpauth://` (`TotpSecret.generateQrCodeUrl`). Es el **frontend** el
> que debe presentar ese secreto al usuario. En esta PoC, el Frontend_Angular
> **renderiza el QR localmente en el navegador** a partir de esa URL (sin
> llamadas de red ni servicios externos de QR, para que el secreto no salga del
> cliente) y, como respaldo, muestra la **clave de configuracion** en texto para
> ingreso manual.

### Paso 4.1 — Iniciar sesión como Cliente

- **Ruta:** Frontend_Angular → pantalla de ingreso → introducir el **correo**
  del usuario Cliente creado en la Sección 2 → continuar.
- **Ruta (ampliada):** la pantalla pide primero el **correo** y, al continuar,
  la **contrasena** del Cliente (ambos preparados en la Seccion 2; recuerde que
  el correo debe estar **verificado** y el usuario debe tener contrasena, lo que
  se logra con `actualizar_usuario --email-verified --password`).
- **Resultado observable:** tras validar la contrasena, si el Cliente **aun no
  esta enrolado** en TOTP, el frontend pasa a la pantalla de **enrolamiento**
  (Paso 4.2); si **ya esta enrolado**, pide directamente el **codigo de 6
  digitos** (verificacion) sin volver a mostrar el QR.

### Paso 4.2 — Mostrar el QR de enrolamiento (renderizado por el frontend)

- **Resultado observable:** la pantalla de enrolamiento muestra un **codigo QR**
  escaneable (generado localmente en el navegador a partir de la URL
  `otpauth://` del secreto TOTP) y, como respaldo, la **clave de configuracion**
  en texto y la URL `otpauth://` (en un desplegable) para ingreso manual.
- **Bloqueo (R8.7):** no aparece el QR ni la pantalla de enrolamiento.
  **Causas probables y acciones correctivas:**
  - TOTP no habilitado en el **tenant** -> completar el Paso 1.6.c y reintentar.
  - El correo del Cliente **no esta verificado** (Firebase exige
    `email_verified=True` para enrolar MFA) -> ejecutar
    `actualizar_usuario --uid <uid> --email-verified --tenant <TENANT_ID>`.
  - El Cliente **no tiene contrasena** -> asignarla con
    `actualizar_usuario --uid <uid> --password <clave> --tenant <TENANT_ID>`.
  - Si en lugar del QR se pide directamente el codigo, el usuario **ya estaba
    enrolado**: use un Cliente nuevo para probar el enrolamiento, o elimine y
    recree el usuario con la CLI.

### Paso 4.3 — Enrolar en Google Authenticator y generar el OTP

- **Ruta en el teléfono:** abrir **Google Authenticator** → **+** (Añadir
  código) → **Escanear código QR** → apuntar al QR del Paso 4.2. Si no puede
  escanear, elegir **Introducir clave de configuración** y teclear la clave
  secreta mostrada.
- **Resultado observable (OTP generado — R8.4):** Google Authenticator muestra
  una nueva entrada con el nombre del Cliente/app y un **código de 6 dígitos**
  que se renueva cada 30 segundos. Introduzca ese código en el prompt de 2FA del
  frontend para completar el inicio de sesión.
- **Bloqueo (R8.7):** el código es rechazado repetidamente. **Causas probables:**
  reloj del teléfono desincronizado, o superación del límite de intentos (tras 5
  intentos inválidos, Firebase bloquea temporalmente ≥ 300 s — R3.7). **Acción
  correctiva:** sincronizar la hora del dispositivo (ajustes de Google
  Authenticator → *Corregir ahora* la sincronización horaria) y esperar a que
  expire el bloqueo antes de reintentar.

---

## Sección 5 — Generar el OTP (Interno) desde el QR de F5 APM

Objetivo (R8.5): un usuario que simula un **Interno** enrola y genera el OTP en
**Google Authenticator** a partir del QR producido por **F5 APM** (no por
Firebase; para Internos el 2FA es corporativo).

### Paso 5.1 — Iniciar sesión como Interno (vía SAML)

- **Ruta:** Frontend_Angular → pantalla de ingreso → introducir el **número de
  identificación** (5–15 dígitos) → continuar. El backend clasifica la ruta como
  **Interno** y el frontend redirige a **F5 APM** (SAML).
- **Resultado observable:** el navegador muestra la pantalla de autenticación de
  **F5 APM**, donde se validan las credenciales contra OpenText IDM.
- **Bloqueo (R8.7):** no hay redirección a F5 APM. **Acción correctiva:**
  verificar que la federación SAML esté establecida (Sección 3) y que
  `<SAML_PROVIDER_ID>` esté habilitado en Identity Platform.

### Paso 5.2 — Mostrar el QR de enrolamiento (producido por F5 APM)

- **Resultado observable:** si el Interno **no está enrolado** en el 2FA
  corporativo, F5 APM muestra un **código QR** de enrolamiento (R4.4). Si ya está
  enrolado, F5 APM solicita directamente el código de 6 dígitos sin volver a
  mostrar el QR (R4.3).
- **Aprovisionamiento (R8.6):** el QR y el 2FA corporativo los provee F5 APM; no
  se configuran en Firebase. La identidad reside en OpenText IDM.

### Paso 5.3 — Enrolar en Google Authenticator y generar el OTP

- **Ruta en el teléfono:** abrir **Google Authenticator** → **+** → **Escanear
  código QR** → apuntar al QR del Paso 5.2 (o introducir la clave de
  configuración si se ofrece).
- **Resultado observable (OTP generado — R8.5):** Google Authenticator muestra
  una entrada con un **código de 6 dígitos** que se renueva cada 30 segundos.
  Introduzca ese código en el prompt de F5 APM para completar el 2FA; tras ello,
  F5 APM emite la **SAML Response firmada** a Firebase y el flujo retorna a la
  app.
- **Bloqueo (R8.7):** el código es rechazado. **Causas probables:** reloj
  desincronizado, o superación del límite de intentos corporativo (tras 3
  intentos inválidos se rechaza el ciclo en curso — R4.10). **Acción
  correctiva:** sincronizar la hora del dispositivo y reiniciar el ciclo de
  autenticación; si persiste, verificar con el administrador de F5 APM el estado
  del enrolamiento del usuario en OpenText IDM.

---

## Sección 6 — Tabla de aprovisionamiento de credenciales y endpoints (R8.6)

Resumen de **qué** se aprovisiona, **dónde** (ubicación exacta) y **qué tipo de
valor** es. El operador aporta todos los valores reales.

| # | Elemento | Ubicación exacta | Tipo de valor | Consumidor |
|---|---|---|---|---|
| 1 | Proyecto GCP | Consola de GCP → selector de proyecto | ID de proyecto (`<PROJECT_ID>`) | Toda la PoC |
| 2 | Facturación | Consola de GCP → **Facturación** | Cuenta de facturación activa | Identity Platform (edición de pago) |
| 3 | Habilitación de Identity Platform | Consola de GCP → **Identity Platform** → *Enable*; incluye habilitar **multi-tenancy** (`multiTenant.allowTenants`, Paso 1.6.a) | — (acción) | Firebase/Identity Platform |
| 4 | MFA TOTP | Vía REST API en **Cloud Shell**, **a nivel de TENANT** (`mfaConfig`, Paso 1.6.c); a nivel de proyecto se deja `mfa.state` en `DISABLED` | Configuración `mfaConfig` habilitada | 2FA de Clientes |
| 5 | Cuenta de servicio de Firebase | Consola de Firebase → **Configuración del proyecto → Cuentas de servicio → Generar nueva clave privada** | Archivo JSON (fuera de VCS) | Backend_FastAPI y CLI_Gestion |
| 6 | Credenciales de la CLI | Variable `GOOGLE_APPLICATION_CREDENTIALS` o argumento `--credentials` | Ruta al JSON de cuenta de servicio | CLI_Gestion |
| 7 | Proveedor SAML (IdP) | Identity Platform → **Identity providers → Add a Provider → SAML** | `<SAML_PROVIDER_ID>` (empieza con `saml.`), `<APM_ENTITY_ID>`, `<APM_SSO_URL>` (HTTPS), `<APM_CERT>` (PEM) | Flujo Interno |
| 8 | Entity ID del SP | Identity Platform → sección **Service provider**; y como *audience* en F5 APM | `<SP_ENTITY_ID>` (URI/URL) | Federación SAML |
| 9 | ACS / callback URL | Identity Platform (campo **Authorization callback**) → registrar en F5 APM | `https://<PROJECT_ID>.firebaseapp.com/__/auth/handler` | Federación SAML |
| 10 | Dominio autorizado | Identity Platform → **Project settings → Add Domain** | `<APP_DOMAIN>` | Redirecciones SAML/OAuth |
| 11 | Metadatos del IdP (F5 APM) | Consola/administrador de **F5 APM** (Access Policy SAML) | Entity ID, SSO URL, certificado de firma | SP (Identity Platform) |
| 12 | URL del Backend (frontend) | `frontend/src/environments/environment*.ts` | URL por entorno (sin IP ni default embebido) | Frontend_Angular |
| 13 | Tenant dedicado | Creado en **Paso 1.6** (Cloud Shell) | `<TENANT_ID>` (p. ej. `poc-auth-2fa-vh5h3`) | Backend (`FIREBASE_TENANT_ID`), Frontend (`config.js` `tenantId`), CLI (`--tenant` / `FIREBASE_TENANT_ID`) |
| 14 | Variable de entorno del backend | Entorno del Backend_FastAPI | `FIREBASE_TENANT_ID=<TENANT_ID>` (el **StartupValidator aborta** si falta) | Backend_FastAPI |
| 15 | Configuración web de Firebase (frontend) | Consola de Firebase → config de la app web → `frontend` (`config.js`) | `apiKey` **público** `AIzaSyCjpoFIT04KLhVop0EmBPLyOCvROMiRhMc`, `authDomain` `sandbox-arquitectura.firebaseapp.com` (**config pública, NO es un secreto**) | Frontend_Angular |

> Recordatorio de seguridad (steering de la organización): nunca escriba
> secretos (clave privada de la cuenta de servicio, certificados privados) en el
> código, en la imagen Docker, en logs ni en el control de versiones. Use
> variables de entorno o Secret Manager. El archivo JSON de cuenta de servicio
> debe quedar fuera de git (ya excluido por `.gitignore`).

---

## Sección 7 — Catálogo de bloqueos y acciones correctivas (R8.7)

Resumen consolidado de las condiciones que impiden avanzar y su remediación.

| Paso | Condición de bloqueo | Acción correctiva antes de reintentar |
|---|---|---|
| 0.1 | Sin acceso/permisos al proyecto GCP | Solicitar al administrador el rol adecuado sobre `<PROJECT_ID>` |
| 0.2 / 1.3 | Facturación no activa | Vincular una cuenta de facturación válida al proyecto |
| 1.5 | No se ve un interruptor de TOTP en la consola | Es esperado: el TOTP se habilita vía **REST API a nivel de tenant** (`mfaConfig`, Paso 1.6.c), no por consola; a nivel de proyecto se deja `DISABLED` |
| 1.6.a/1.6.b | `tenants` devuelve `INVALID_PROJECT_ID` | Multi-tenancy no habilitada: ejecutar el PATCH `multiTenant.allowTenants = true` (Paso 1.6.a) y reintentar |
| 1.6 | `403`/permiso denegado al administrar tenants | Solicitar el rol de administrador de Identity Platform |
| 2.0 | Credenciales de la CLI ausentes/ inválidas | Definir `GOOGLE_APPLICATION_CREDENTIALS` o `--credentials` apuntando a un JSON de cuenta de servicio vigente |
| 2.0 | Tenant ausente en la CLI (sin `FIREBASE_TENANT_ID` ni `--tenant`) | La CLI aborta antes de cualquier cambio: exportar `FIREBASE_TENANT_ID=<TENANT_ID>` o pasar `--tenant <TENANT_ID>` |
| 6 (backend) | Backend aborta al iniciar por `FIREBASE_TENANT_ID` ausente | Definir la variable de entorno `FIREBASE_TENANT_ID=<TENANT_ID>` (el StartupValidator lo exige) |
| 2.1 | Correo duplicado al crear | Usar otro correo o administrar el usuario existente |
| 2.2 / 2.3 | `uid` inexistente | Verificar el `UID` en la consola de Firebase y reintentar |
| 3.2 | Faltan metadatos del IdP (F5 APM) | Solicitar al administrador de F5 APM Entity ID, SSO URL y certificado |
| 3.4 | Sin acceso de administración a F5 APM | Coordinar con el administrador el registro del SP |
| 3.5 | Firma SAML inválida/expirada | Cargar el certificado de firma vigente del IdP; rotar si expiró |
| 3.5 | Audience/Entity ID no coincide | Igualar `<SP_ENTITY_ID>` en ambos extremos |
| 3.5 | Dominio no autorizado | Añadir `<APP_DOMAIN>` en Project settings → Add Domain |
| 4.2 | No aparece el QR del Cliente | Habilitar TOTP en el tenant (Paso 1.6.c); verificar que el correo este verificado y el usuario tenga contrasena (`actualizar_usuario --email-verified --password`) |
| 4.3 / 5.3 | OTP rechazado | Sincronizar la hora del teléfono; esperar a que expire el bloqueo por intentos |
| 5.1 | Sin redirección a F5 APM | Verificar que la federación SAML y `<SAML_PROVIDER_ID>` estén habilitados |

---

## Sección 8 — Ejecución local validada (flujo Cliente de extremo a extremo)

Esta sección documenta el procedimiento **verificado** para levantar la PoC en
una máquina local y validar el flujo **Cliente** (correo → contraseña →
enrolamiento TOTP con QR → verificación → dashboard). Resume lo que efectivamente
se ejecutó durante la validación de la PoC.

> **Nota de entorno local:** para desarrollo local se aplican desviaciones
> respecto de las reglas corporativas (registro de dependencias, atributos de la
> cookie, CORS). Están catalogadas, con su reversión, en
> `docs/desviaciones-locales.md`. **Revísela y revierta antes de publicar.**

### Prerrequisitos locales

- El tenant dedicado creado y con TOTP habilitado (Sección 1.6).
- La cuenta de servicio de Firebase descargada **fuera del repositorio** y la
  ruta exportada en `GOOGLE_APPLICATION_CREDENTIALS`.
- Un usuario Cliente de prueba, **con correo verificado y contraseña**, dentro
  del tenant. Para prepararlo con la CLI:

```bash
# Crear (si no existe) y luego dejarlo listo para TOTP:
poetry run crear_usuario --email <cliente@ejemplo.com> --tenant <TENANT_ID>
poetry run actualizar_usuario --uid <uid> --email-verified --password <clave> --tenant <TENANT_ID>
```

### Paso 8.1 — Arrancar el Backend_FastAPI

- **Puerto:** la validación usó el puerto **8081** (el `8080` estaba ocupado en
  la máquina). Ajuste el puerto según su entorno.
- **Variables de entorno locales:** `APP_ENV=local` (relaja la cookie a
  no-`Secure` + `SameSite=Lax` y habilita CORS para el dev server), más
  `GOOGLE_APPLICATION_CREDENTIALS`, `FIREBASE_PROJECT_ID` y `FIREBASE_TENANT_ID`.
- **Resultado observable:** `GET /health` responde `200` y el log de arranque no
  reporta variables de entorno faltantes.

### Paso 8.2 — Arrancar el Frontend_Angular (con proxy de desarrollo)

- El `config.js` local usa `apiBaseUrl` **relativo** (`/api/v1`) y el proxy de
  Angular (`frontend/proxy.conf.json`) reenvía `/api` al backend
  (`http://localhost:8081`). Así, el navegador ve backend y frontend en el
  **mismo origen** (`localhost:4200`) y la cookie de sesión fluye sin problemas
  de `SameSite` cross-site.
- **Comando:** `ng serve --proxy-config proxy.conf.json`.
- **Resultado observable:** la aplicación carga en `http://localhost:4200` y la
  pantalla de ingreso aparece sin errores de configuración.

### Paso 8.3 — Validar el flujo Cliente de extremo a extremo

1. Ingresar el **correo** del Cliente de prueba → **Continuar**.
2. Ingresar la **contraseña** → **Continuar**.
3. **Enrolamiento (primera vez):** la pantalla muestra el **código QR**
   (renderizado localmente, ver Sección 4). Escanearlo con Google Authenticator
   (o usar la clave de configuración) y escribir el **código de 6 dígitos** →
   **Confirmar**.
4. **Resultado observable:** la aplicación establece la sesión (cookie
   `HttpOnly`) y navega al **dashboard**, que muestra el nombre, el tipo de
   usuario (**Cliente**) y los claims con **PII enmascarada** (p. ej. el correo
   como `****...`).
5. **Verificación (inicios posteriores):** el mismo usuario ya enrolado pedirá
   solo el código de 6 dígitos (sin QR) antes de llegar al dashboard.

### Paso 8.4 — Bloqueos locales frecuentes (R8.7)

| Síntoma | Causa probable | Acción correctiva |
|---|---|---|
| Dashboard responde `401` tras enrolar | La session cookie se verifica a nivel proyecto y el usuario vive en el tenant | Confirmar que `verify_session_cookie` use `check_revoked=False` (decisión PoC documentada) y que `FIREBASE_TENANT_ID` esté configurado |
| El navegador "se queda" en el correo y no pide contraseña | El usuario de prueba no tiene contraseña, o el correo no está verificado | `actualizar_usuario --uid <uid> --email-verified --password <clave> --tenant <TENANT_ID>` |
| La cookie no se guarda | Front y back en orígenes distintos (cross-site) | Usar el proxy de Angular (`--proxy-config proxy.conf.json`) y `apiBaseUrl` relativo |
| El puerto 8080 falla al arrancar | Puerto ocupado por otra app | Usar otro puerto (p. ej. 8081) |

---

## Referencias

- Documentación de Google Cloud Identity Platform — habilitación de MFA TOTP y
  configuración de proveedores SAML (consola de Identity Platform: *Identity
  providers → Add a Provider → SAML*; URL de callback por defecto
  `https://PROJECT-ID.firebaseapp.com/__/auth/handler`). *El contenido fue
  parafraseado y resumido a partir de la documentación oficial de Google Cloud
  para cumplir con las restricciones de licenciamiento.*
- "Set up multi-tenancy in Identity Platform" —
  <https://docs.cloud.google.com/identity-platform/docs/multi-tenancy-quickstart>
  (habilitar multi-tenancy y crear/administrar tenants). *El contenido fue
  parafraseado y resumido a partir de la documentación oficial de Google Cloud
  para cumplir con las restricciones de licenciamiento.*
- "Enable TOTP MFA" —
  <https://docs.cloud.google.com/identity-platform/docs/admin/enabling-totp-mfa>
  (habilitación del MFA TOTP vía REST API / Admin SDK, a nivel de proyecto o de
  tenant). *El contenido fue parafraseado y resumido a partir de la documentación
  oficial de Google Cloud para cumplir con las restricciones de licenciamiento.*
- Diseño de la PoC: `.kiro/specs/auth-2fa-poc/design.md` (flujo Cliente con
  `signInWithEmailAndPassword` + TOTP; flujo Interno con `signInWithRedirect` +
  `SAMLAuthProvider`; verificación de ID token y creación de session cookie en el
  Backend_FastAPI).
- CLI_Gestion: `cli/README.md` y `cli/src/cli/` (comandos `crear_usuario`,
  `actualizar_usuario`, `eliminar_usuario`).
