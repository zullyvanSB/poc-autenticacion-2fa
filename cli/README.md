# CLI_Gestion

Scripts de línea de comandos para el alta, actualización y baja de usuarios
Cliente en Firebase, dentro de la PoC de autenticación y 2FA (TOTP).

## Descripción

La CLI_Gestion administra la población de usuarios Cliente directamente contra
Firebase mediante el **Firebase Admin SDK** (`firebase-admin`). No existen
endpoints administrativos en el Backend_FastAPI (R7.6): toda la gestión ocurre
por esta CLI o por la consola de Firebase.

Las operaciones ocurren **dentro de un tenant dedicado de Google Cloud Identity
Platform** (p. ej. `poc-auth-2fa-vh5h3`), de modo que los usuarios de prueba de
la PoC quedan aislados del resto de cuentas del proyecto. El identificador del
tenant se resuelve desde configuración (nunca se codifica en el código fuente).

Comandos previstos:

| Comando | Descripción |
|---|---|
| `crear_usuario --email <correo>` | Crea un usuario Cliente. |
| `actualizar_usuario --uid <uid> [--email ...] [--display-name ...] [--email-verified \| --no-email-verified] [--password ...]` | Actualiza un usuario existente. |
| `eliminar_usuario --uid <uid>` | Elimina un usuario existente. |

### Campos actualizables de `actualizar_usuario`

Debe proporcionarse al menos uno de los campos modificables; en caso contrario
la operación aborta como validación de parámetros sin tocar Firebase (R7.7).

| Flag | Descripción |
|---|---|
| `--email <correo>` | Nuevo correo electrónico (opcional). |
| `--display-name <nombre>` | Nuevo nombre para mostrar (opcional). |
| `--email-verified` / `--no-email-verified` | Marca o desmarca el correo como verificado (opcional). Firebase exige `email_verified=True` **antes** de poder enrolar TOTP/MFA en el usuario Cliente. |
| `--password <contraseña>` | Nueva contraseña (mínimo 6 caracteres, opcional). Necesaria para el inicio de sesión con correo y contraseña del flujo TOTP. **Nunca se registra ni se imprime** en la salida. |

```bash
# Preparar un usuario de prueba para el flujo TOTP: verificar el correo y fijar
# una contraseña (la contraseña nunca aparece en la salida ni en logs).
poetry run actualizar_usuario --uid <uid> --email-verified --password "ContraseñaDePrueba"
```

Todos los comandos aceptan además `--tenant <tenant_id>` (ver sección «Tenant de
Identity Platform») y `--credentials <ruta>`.

## Requisitos

- Python 3.12+
- Poetry
- Acceso al repositorio institucional JFrog (configurado en Poetry)

## Instalación

```bash
poetry install
```

Las dependencias se resuelven exclusivamente desde JFrog y están fijadas a
versión exacta en `pyproject.toml`.

## Credenciales

La CLI obtiene las credenciales de Firebase desde (en orden de precedencia):

1. El argumento `--credentials <ruta>`.
2. La variable de entorno `GOOGLE_APPLICATION_CREDENTIALS`.

El archivo de credenciales (cuenta de servicio) **debe permanecer fuera del
control de versiones** (R7.8). El `.gitignore` excluye los archivos `*.json` de
credenciales. Si las credenciales no se encuentran o son inválidas, la CLI
aborta sin alterar datos y sin exponer el valor de las credenciales (R7.9).

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/ruta/segura/service-account.json
```

## Tenant de Identity Platform

Todas las operaciones se ejecutan dentro de un tenant dedicado. El identificador
del tenant es **obligatorio** y se resuelve desde (en orden de precedencia):

1. El argumento `--tenant <tenant_id>`.
2. La variable de entorno `FIREBASE_TENANT_ID`.

La bandera `--tenant` tiene precedencia sobre la variable de entorno. Si no se
configura ninguno de los dos, la CLI aborta antes de tocar Firebase, indicando
que el tenant es requerido (sin exponer secretos). El tenant nunca se codifica
en el código fuente.

```bash
export FIREBASE_TENANT_ID=poc-auth-2fa-vh5h3
```

| Flag / Variable | Descripción |
|---|---|
| `--tenant <tenant_id>` | Tenant de Identity Platform. Precede a `FIREBASE_TENANT_ID`. |
| `FIREBASE_TENANT_ID` | Tenant por defecto cuando se omite `--tenant`. |

## Uso

```bash
# Usando la variable de entorno FIREBASE_TENANT_ID
export FIREBASE_TENANT_ID=poc-auth-2fa-vh5h3
poetry run crear_usuario --email cliente@ejemplo.com
poetry run actualizar_usuario --uid <uid> --display-name "Nombre Nuevo"
poetry run eliminar_usuario --uid <uid>

# O especificando el tenant explícitamente por bandera
poetry run crear_usuario --tenant poc-auth-2fa-vh5h3 --email cliente@ejemplo.com
```

El parseo rechaza flags desconocidos y claves no esperadas (R7.7): cualquier
parámetro inválido, incompleto o inesperado aborta la operación identificando el
parámetro problemático, sin tocar datos en Firebase. El orden de ejecución valida
los parámetros, luego resuelve el tenant y las credenciales, y solo entonces
ejecuta la operación dentro del tenant; si el tenant falta, la operación aborta
antes de cualquier mutación.

## Autores

Equipo PoC Autenticación 2FA.
