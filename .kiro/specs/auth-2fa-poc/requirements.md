# Requirements Document

## Introduction

Esta funcionalidad es una Prueba de Concepto (PoC) de autenticación y segundo factor de autenticación (2FA) basado en TOTP para una organización aseguradora. La PoC debe operar primero en entorno local y posteriormente desplegarse en Google Cloud Platform (Cloud Run para el backend y Firebase Hosting para el frontend Angular). Ningún componente se simula ni se mockea: todos los componentes son reales y el usuario aprovisionará credenciales y endpoints cuando el flujo lo requiera.

La PoC contempla dos poblaciones de usuarios con rutas de autenticación distintas, pero una experiencia de ingreso unificada y transparente:

1. **Clientes**: se autentican con su correo electrónico. La gestión de identidad y el 2FA (TOTP) los resuelve Firebase / Google Cloud Identity Platform (edición de pago que soporta MFA TOTP y federación SAML).
2. **Internos** (Funcionarios y externos de outsourcing/temporales/proveedores): se autentican con su número de identificación. La identidad reside en OpenText IDM; F5 APM (Access Manager) federa contra OpenText IDM y provee el 2FA corporativo generando el código QR para Google Authenticator. Firebase (Identity Platform) actúa como Service Provider SAML que federa hacia F5 APM para esta población.

El backend (FastAPI) decide de forma transparente la ruta (Cliente o Interno) a partir del identificador ingresado, implementa el flujo OIDC Authorization Code (manteniendo los secretos del lado del servidor), verifica tokens y protege el recurso del dashboard. El frontend (Angular) presenta un ingreso de un solo campo (identifier-first), luego solicita el 2FA y finalmente muestra un portal protegido con los datos del usuario autenticado.

La guía operativa paso a paso es un entregable central de la PoC: habilitar la edición de Google Cloud Identity Platform en el sandbox de GCP, crear y administrar usuarios Cliente en Firebase, configurar la federación SAML con F5 APM, y generar el OTP en Google Authenticator a partir del QR producido por el componente responsable (Firebase para Clientes, F5 APM para Internos).

## Glossary

- **PoC**: Prueba de Concepto. Alcance acotado para validar la viabilidad técnica de la autenticación y 2FA.
- **Sistema_Autenticacion**: Conjunto de la solución PoC (Frontend_Angular + Backend_FastAPI + proveedores de identidad) que ejecuta la autenticación y el 2FA.
- **Frontend_Angular**: Aplicación cliente desarrollada en Angular que presenta el ingreso identifier-first, el prompt de 2FA y el dashboard protegido.
- **Backend_FastAPI**: Servicio en Python/FastAPI que decide el enrutamiento, ejecuta el intercambio de código por tokens OIDC, verifica tokens y protege el recurso del dashboard.
- **Cliente**: Usuario de tipo cliente de la aseguradora que se autentica con correo electrónico. Su identidad y 2FA TOTP los administra Firebase.
- **Interno**: Usuario funcionario o externo (outsourcing, temporal, proveedor) que se autentica con número de identificación. Su identidad reside en OpenText IDM y su 2FA lo provee F5 APM.
- **Identificador**: Dato único ingresado por el usuario en el campo único de ingreso; puede ser un correo electrónico (Cliente) o un número de identificación (Interno).
- **Firebase**: Google Cloud Identity Platform (edición de pago) que actúa como proveedor OIDC (OP) para Clientes y como Service Provider SAML para Internos.
- **F5_APM**: F5 Access Policy Manager, que federa contra OpenText IDM y provee el 2FA corporativo generando el QR para Google Authenticator de los Internos.
- **OpenText_IDM**: Sistema de gestión de identidad corporativa donde residen las credenciales de los Internos, validado vía LDAP/Kerberos por F5 APM.
- **TOTP**: Time-based One-Time Password. Código temporal de un solo uso usado como segundo factor.
- **Google_Authenticator**: Aplicación generadora de códigos TOTP a partir de un QR provisto por el componente responsable.
- **OIDC**: OpenID Connect. Protocolo de autenticación sobre OAuth 2.0 usado por el Backend_FastAPI.
- **Authorization_Code_Flow**: Flujo de OAuth 2.0/OIDC en el que el Backend_FastAPI intercambia un código de autorización por tokens JWT del lado del servidor.
- **JWT**: JSON Web Token. Token firmado que contiene los claims del usuario autenticado.
- **SAML**: Security Assertion Markup Language. Protocolo de federación entre Firebase (SP) y F5 APM (IdP) para los Internos.
- **Dashboard**: Recurso protegido mostrado tras la autenticación exitosa; presenta nombre, tipo de usuario (Cliente/Interno) y claims del token.
- **CLI_Gestion**: Conjunto de scripts de línea de comandos para alta, baja y actualización de usuarios Cliente en Firebase.
- **Guia_Operativa**: Entregable documental con los pasos para habilitar, configurar y operar los componentes de la PoC.

## Requirements

### Requisito 1: Ingreso unificado identifier-first

**Historia de Usuario:** Como usuario (Cliente o Interno), quiero ingresar mi identificador en un único campo sin elegir mi tipo de usuario, para iniciar sesión de forma transparente y sencilla.

#### Criterios de Aceptación

1. THE Frontend_Angular SHALL presentar una pantalla de ingreso con un único campo de identificador editable, sin selector de tipo de usuario ni campos adicionales de credenciales.
2. WHEN el usuario envía el Identificador con contenido no vacío tras recortar espacios en blanco iniciales y finales, THE Frontend_Angular SHALL transmitir el Identificador normalizado al Backend_FastAPI para la decisión de enrutamiento.
3. IF el campo de Identificador se envía vacío o compuesto únicamente por espacios en blanco, THEN THE Frontend_Angular SHALL mostrar un mensaje de validación indicando que el Identificador es obligatorio y SHALL abstenerse de enviar la solicitud al Backend_FastAPI.
4. IF el Identificador ingresado excede 254 caracteres, THEN THE Frontend_Angular SHALL mostrar un mensaje de validación indicando que el Identificador supera la longitud máxima permitida y SHALL abstenerse de enviar la solicitud al Backend_FastAPI.
5. WHEN el Backend_FastAPI responde con la ruta de autenticación determinada dentro de 15 segundos desde el envío, THE Frontend_Angular SHALL continuar el flujo de autenticación correspondiente sin requerir que el usuario seleccione su tipo.
6. IF el Backend_FastAPI no responde dentro de 15 segundos o responde con un error de enrutamiento, THEN THE Frontend_Angular SHALL mostrar un mensaje de error indicando que el ingreso no pudo procesarse, SHALL conservar el Identificador ingresado por el usuario y SHALL permitir reintentar el envío.

### Requisito 2: Decisión de enrutamiento en el backend

**Historia de Usuario:** Como arquitecto de la solución, quiero que el backend decida la ruta Cliente o Interno a partir del identificador, para mantener la lógica de negocio fuera del frontend y la experiencia transparente para el usuario.

#### Criterios de Aceptación

1. WHEN el Backend_FastAPI recibe un Identificador cuyo formato corresponde a un correo electrónico válido (según el estándar RFC 5322, con longitud entre 6 y 254 caracteres), THE Backend_FastAPI SHALL clasificar la solicitud como ruta Cliente y dirigir la autenticación hacia Firebase.
2. WHEN el Backend_FastAPI recibe un Identificador cuyo formato corresponde a un número de identificación válido (cadena compuesta únicamente por dígitos, con longitud entre 5 y 15 caracteres), THE Backend_FastAPI SHALL clasificar la solicitud como ruta Interno y dirigir la autenticación hacia la federación SAML de Firebase hacia F5_APM.
3. THE Backend_FastAPI SHALL validar en el servidor que el Identificador recibido cumpla tipo (cadena de texto), longitud (entre 5 y 254 caracteres) y formato (correo electrónico válido o número de identificación numérico) antes de resolver la ruta.
4. IF el Identificador recibido está vacío, excede 254 caracteres, o no coincide con el formato de correo electrónico ni con el formato de número de identificación numérico, THEN THE Backend_FastAPI SHALL rechazar la solicitud sin resolver la ruta, SHALL responder con un error genérico que indique que el identificador es inválido sin revelar el motivo específico ni el valor recibido, y SHALL registrar el detalle del rechazo internamente asociado a un Correlation-ID.
5. WHEN el Backend_FastAPI resuelve la ruta de enrutamiento (Cliente o Interno), THE Backend_FastAPI SHALL registrar la decisión asociada a un Correlation-ID, excluyendo del registro el valor del Identificador y cualquier otro dato personal sensible.
6. IF el Backend_FastAPI recibe una solicitud de enrutamiento sin un Correlation-ID asociado, THEN THE Backend_FastAPI SHALL generar un Correlation-ID único para la solicitud antes de registrar la decisión o el rechazo.

### Requisito 3: Autenticación y TOTP de Clientes vía Firebase

**Historia de Usuario:** Como Cliente, quiero autenticarme con mi correo y un código TOTP, para acceder de forma segura al recurso protegido.

#### Criterios de Aceptación

1. WHEN el Backend_FastAPI clasifica la solicitud como ruta Cliente, THE Sistema_Autenticacion SHALL iniciar el flujo de autenticación de Firebase para el correo electrónico del Cliente dentro de los 15 segundos siguientes a la recepción de la solicitud.
2. WHEN las credenciales del Cliente son válidas en Firebase, THE Sistema_Autenticacion SHALL solicitar al Cliente un código TOTP de 6 dígitos numéricos como segundo factor.
3. WHEN el Cliente envía un código TOTP de 6 dígitos numéricos válido y vigente dentro de la ventana de validez de 30 segundos, THE Firebase SHALL completar la autenticación y emitir un código de autorización hacia el Backend_FastAPI.
4. IF las credenciales del Cliente son inválidas, THEN THE Sistema_Autenticacion SHALL rechazar el acceso, mostrar un mensaje de error genérico al usuario sin revelar cuál factor falló, y no emitir código de autorización.
5. IF el código TOTP enviado por el Cliente es inválido, expirado, o no tiene exactamente 6 dígitos numéricos, THEN THE Sistema_Autenticacion SHALL rechazar el acceso, mostrar un mensaje de error genérico al usuario sin revelar cuál factor falló, y no emitir código de autorización.
6. WHEN un Cliente se inscribe por primera vez en 2FA, THE Firebase SHALL producir un QR para enrolar el TOTP en Google_Authenticator.
7. IF el Cliente supera 5 intentos consecutivos de código TOTP inválido, THEN THE Sistema_Autenticacion SHALL bloquear temporalmente nuevos intentos durante al menos 300 segundos y mostrar un mensaje de error genérico al usuario.

### Requisito 4: Autenticación y 2FA de Internos vía Firebase SAML y F5 APM

**Historia de Usuario:** Como Interno (Funcionario o externo), quiero autenticarme con mi número de identificación y el 2FA corporativo, para acceder al recurso protegido usando la identidad corporativa.

#### Criterios de Aceptación

1. WHEN el Backend_FastAPI clasifica la solicitud como ruta Interno, THE Firebase SHALL emitir una solicitud de autenticación SAML (AuthnRequest) hacia F5_APM actuando como Service Provider dentro de los 15 segundos siguientes a la recepción de la solicitud.
2. WHEN F5_APM recibe la AuthnRequest, THE F5_APM SHALL validar las credenciales del Interno contra OpenText_IDM, esperando la respuesta de OpenText_IDM durante un máximo de 15 segundos por intento.
3. WHILE el Interno ya se encuentra enrolado en el 2FA corporativo, THE F5_APM SHALL solicitar directamente el código de 6 dígitos numéricos del 2FA corporativo sin volver a generar el QR de enrolamiento.
4. WHEN las credenciales del Interno son válidas y el Interno no está enrolado en el 2FA corporativo, THE F5_APM SHALL generar el QR para enrolar el 2FA corporativo en Google_Authenticator antes de solicitar el código.
5. WHEN el Interno completa el 2FA corporativo con un código de 6 dígitos numéricos válido y vigente, THE F5_APM SHALL emitir una respuesta SAML firmada hacia Firebase dentro de los 15 segundos siguientes a la validación del código.
6. WHEN Firebase recibe una respuesta SAML cuya firma digital es válida y cuya vigencia no ha expirado, THE Firebase SHALL emitir un código de autorización hacia el Backend_FastAPI.
7. IF Firebase recibe una respuesta SAML con firma digital inválida, no verificable o expirada, THEN THE Sistema_Autenticacion SHALL rechazar el acceso, no emitir código de autorización, no establecer sesión y mostrar un mensaje de error genérico al usuario sin revelar el motivo específico.
8. IF la validación de credenciales en OpenText_IDM falla, o F5_APM u OpenText_IDM no responden dentro de los 15 segundos, THEN THE Sistema_Autenticacion SHALL rechazar el acceso, no emitir código de autorización, no establecer sesión y mostrar un mensaje de error genérico al usuario.
9. IF el código del 2FA corporativo enviado por el Interno es inválido, expirado o no tiene exactamente 6 dígitos numéricos, THEN THE Sistema_Autenticacion SHALL rechazar el intento, no emitir código de autorización, no establecer sesión y mostrar un mensaje de error genérico al usuario.
10. IF el Interno supera 3 intentos consecutivos de código del 2FA corporativo inválido, THEN THE Sistema_Autenticacion SHALL rechazar nuevos intentos del ciclo de autenticación en curso, no emitir código de autorización y mostrar un mensaje de error genérico al usuario.

### Requisito 5: Intercambio de código por tokens en el backend (Authorization Code Flow)

**Historia de Usuario:** Como responsable de seguridad, quiero que el backend intercambie el código de autorización por tokens JWT manteniendo los secretos del lado del servidor, para evitar exponer credenciales en el cliente.

#### Criterios de Aceptación

1. WHEN el Backend_FastAPI recibe un código de autorización, THE Backend_FastAPI SHALL intercambiarlo por tokens JWT mediante el Authorization_Code_Flow de OIDC del lado del servidor, completando el intercambio con el proveedor de identidad en 15 segundos o menos.
2. THE Backend_FastAPI SHALL mantener los secretos de cliente OIDC exclusivamente del lado del servidor, obtenidos de variables de entorno o Secret Manager, sin transmitirlos ni exponerlos al Frontend_Angular ni incluirlos en respuestas, logs o mensajes de error.
3. WHEN el Backend_FastAPI recibe un token JWT, THE Backend_FastAPI SHALL verificar la firma, el emisor, la audiencia y la vigencia del token aplicando una tolerancia de reloj máxima de 60 segundos antes de aceptarlo.
4. IF el intercambio del código de autorización falla, o si F5_APM o Firebase no responden dentro de los 15 segundos, THEN THE Backend_FastAPI SHALL denegar el acceso, no establecer sesión y responder con un error genérico de autenticación sin revelar el motivo específico ni detalles de implementación.
5. IF el token JWT presenta firma inválida, emisor o audiencia no esperados, o se encuentra expirado considerando la tolerancia de reloj, THEN THE Backend_FastAPI SHALL denegar el acceso, no establecer sesión y responder con un error genérico de autenticación sin revelar el motivo específico.
6. WHEN el Backend_FastAPI acepta un token JWT válido, THE Backend_FastAPI SHALL entregar la sesión autenticada al Frontend_Angular mediante una cookie con los atributos Secure, HttpOnly y SameSite=Strict y con una expiración acotada, sin almacenar tokens en localStorage ni devolverlos en el cuerpo de la respuesta.

### Requisito 6: Recurso protegido (Dashboard)

**Historia de Usuario:** Como usuario autenticado, quiero ver un portal con mis datos, para confirmar que el acceso al recurso protegido fue concedido.

#### Criterios de Aceptación

1. WHEN un usuario autenticado accede al Dashboard, THE Frontend_Angular SHALL mostrar el nombre del usuario, el tipo de usuario (con valor igual a "Cliente" o "Interno") y la lista de claims del token, renderizando el contenido en 3 segundos o menos tras recibir la respuesta del Backend_FastAPI.
2. WHEN el Frontend_Angular solicita datos del Dashboard, THE Backend_FastAPI SHALL validar que la solicitud incluya una sesión autenticada válida y no expirada antes de generar la respuesta.
3. IF una solicitud al Dashboard carece de una sesión autenticada o presenta una sesión expirada o inválida, THEN THE Backend_FastAPI SHALL responder con un error de no autorizado, SHALL abstenerse de incluir cualquier dato del Dashboard en la respuesta y SHALL conservar sin modificar los datos de la sesión del usuario.
4. THE Backend_FastAPI SHALL enmascarar los datos personales sensibles presentes en los claims, dejando visibles como máximo los últimos 4 caracteres de cada valor sensible, antes de entregarlos al Frontend_Angular.
5. WHEN el usuario cierra sesión, THE Sistema_Autenticacion SHALL invalidar la sesión autenticada de forma que cualquier solicitud posterior con esa sesión sea rechazada como no autorizada, y SHALL redirigir al Frontend_Angular a la pantalla de ingreso en 3 segundos o menos.
6. IF un usuario autenticado accede al Dashboard y no existen claims disponibles en el token, THEN THE Frontend_Angular SHALL mostrar un mensaje que indique la ausencia de datos y SHALL abstenerse de mostrar campos vacíos sin indicación.

### Requisito 7: Gestión de usuarios Cliente en Firebase vía CLI y consola

**Historia de Usuario:** Como operador de la PoC, quiero crear, actualizar y eliminar usuarios Cliente mediante scripts de línea de comandos y la consola de Firebase, para administrar la población de prueba sin exponer endpoints administrativos.

#### Criterios de Aceptación

1. WHEN el operador ejecuta el comando de alta de la CLI_Gestion con un correo electrónico con formato válido (RFC 5322, longitud de 5 a 254 caracteres) que no corresponde a un usuario Cliente existente, THE CLI_Gestion SHALL crear el usuario Cliente en Firebase y retornar un identificador único del usuario creado junto con un indicador de éxito.
2. IF el comando de alta de la CLI_Gestion recibe un correo electrónico que ya corresponde a un usuario Cliente existente en Firebase, THEN THE CLI_Gestion SHALL abortar la operación sin crear ni modificar el usuario y mostrar un mensaje de error que indique la duplicidad del correo.
3. WHEN el operador ejecuta el comando de actualización de la CLI_Gestion sobre un identificador de usuario Cliente existente con datos de formato válido, THE CLI_Gestion SHALL actualizar los datos del usuario Cliente en Firebase y retornar un indicador de éxito.
4. WHEN el operador ejecuta el comando de baja de la CLI_Gestion sobre un identificador de usuario Cliente existente, THE CLI_Gestion SHALL eliminar el usuario Cliente de Firebase y retornar un indicador de éxito.
5. IF un comando de actualización o de baja de la CLI_Gestion recibe un identificador de usuario Cliente que no existe en Firebase, THEN THE CLI_Gestion SHALL abortar la operación sin alterar datos y mostrar un mensaje de error que indique que el usuario no fue encontrado.
6. THE Sistema_Autenticacion SHALL restringir la gestión de usuarios Cliente a la CLI_Gestion y a la consola de Firebase, sin exponer endpoints administrativos en el Backend_FastAPI.
7. IF un comando de la CLI_Gestion recibe parámetros inválidos, incompletos o claves no esperadas, THEN THE CLI_Gestion SHALL abortar la operación sin alterar datos en Firebase y mostrar un mensaje de error que identifique el parámetro inválido o faltante.
8. THE CLI_Gestion SHALL obtener las credenciales de acceso a Firebase desde variables de entorno o un archivo de credenciales ubicado fuera del control de versiones.
9. IF la CLI_Gestion no encuentra credenciales de Firebase en las variables de entorno ni en el archivo de credenciales, o estas son rechazadas por Firebase, THEN THE CLI_Gestion SHALL abortar la operación sin alterar datos y mostrar un mensaje de error que indique la ausencia o invalidez de las credenciales, sin exponer el valor de las credenciales en la salida.

### Requisito 8: Guía operativa paso a paso

**Historia de Usuario:** Como operador de la PoC con acceso a la consola de GCP pero sin gcloud CLI, quiero una guía paso a paso, para habilitar, configurar y operar los componentes de la PoC por mí mismo.

#### Criterios de Aceptación

1. THE Guia_Operativa SHALL incluir los pasos numerados y secuenciales para verificar y habilitar, exclusivamente mediante la consola de GCP sin uso de gcloud CLI, la edición de Google Cloud Identity Platform que soporta MFA TOTP y federación SAML en el sandbox de GCP, indicando para cada paso la ruta de navegación en la consola y el resultado observable esperado que confirma su finalización.
2. THE Guia_Operativa SHALL incluir los pasos numerados y secuenciales para crear y administrar usuarios Cliente en Firebase mediante la CLI_Gestion y la consola, especificando para cada operación las entradas requeridas y el resultado observable esperado que confirma su finalización.
3. THE Guia_Operativa SHALL incluir los pasos numerados y secuenciales para configurar la federación SAML entre Firebase como Service Provider y F5_APM como Identity Provider, especificando los parámetros de configuración que el operador debe introducir en cada extremo y el resultado observable esperado que confirma el establecimiento de la federación.
4. THE Guia_Operativa SHALL incluir los pasos numerados y secuenciales para que un usuario que simula un Cliente genere el OTP en Google_Authenticator a partir del QR producido por Firebase, indicando el resultado observable esperado que confirma que el OTP fue generado.
5. THE Guia_Operativa SHALL incluir los pasos numerados y secuenciales para que un usuario que simula un Interno genere el OTP en Google_Authenticator a partir del QR producido por F5_APM, indicando el resultado observable esperado que confirma que el OTP fue generado.
6. THE Guia_Operativa SHALL indicar, para cada punto del flujo en el que el operador debe aprovisionar credenciales o endpoints de F5_APM, Firebase y GCP, la ubicación exacta en la consola o herramienta donde se realiza el aprovisionamiento y el valor o tipo de valor que debe introducirse.
7. IF un paso de la Guia_Operativa no puede completarse porque un prerrequisito, credencial o endpoint no está disponible, THEN THE Guia_Operativa SHALL indicar la condición que impide continuar y la acción correctiva que el operador debe ejecutar antes de reintentar el paso.

### Requisito 9: Portabilidad de despliegue local a GCP

**Historia de Usuario:** Como responsable del despliegue, quiero que la PoC corra primero en local y luego en GCP sin cambios de código, para validar la solución de forma incremental.

#### Criterios de Aceptación

1. THE Sistema_Autenticacion SHALL ejecutarse en un entorno local con todos los componentes reales (Backend_FastAPI y Frontend_Angular) conectados a los proveedores de identidad reales, sin usar stubs ni simuladores.
2. THE Backend_FastAPI SHALL empaquetarse en una única imagen Docker, idéntica en digest, apta para ejecutarse tanto localmente como en Cloud Run sin reconstrucción.
3. THE Backend_FastAPI SHALL obtener la totalidad de su configuración y secretos exclusivamente de variables de entorno, sin valores embebidos en el código ni en la imagen, de modo que el mismo artefacto opere en local y en GCP sin cambios de código.
4. IF una variable de entorno requerida está ausente o vacía al iniciar, THEN THE Backend_FastAPI SHALL abortar el arranque e indicar el nombre de cada variable faltante, sin exponer el valor de ningún secreto.
5. WHERE el despliegue es en GCP, THE Backend_FastAPI SHALL desplegarse en Cloud Run y THE Frontend_Angular SHALL desplegarse en Firebase Hosting.
6. THE Frontend_Angular SHALL resolver la URL del Backend_FastAPI mediante configuración por entorno, sin direcciones IP ni URLs estáticas embebidas en el código fuente.
7. WHEN el Frontend_Angular inicia sin una URL de Backend_FastAPI resuelta por configuración de entorno, THE Frontend_Angular SHALL impedir las llamadas al backend e indicar una condición de error de configuración, sin recurrir a una dirección por defecto embebida.

### Requisito 10: Seguridad y observabilidad transversales

**Historia de Usuario:** Como responsable de seguridad, quiero que la PoC aplique controles de seguridad y trazabilidad, para proteger los datos y permitir el diagnóstico sin exponer información sensible.

#### Criterios de Aceptación

1. WHEN el Backend_FastAPI recibe una solicitud sin Correlation-ID, THE Backend_FastAPI SHALL generar un identificador único (UUID v4) y asociarlo a la solicitud durante todo su procesamiento.
2. WHEN el Backend_FastAPI emite una solicitud hacia otro servicio o proveedor de identidad, THE Backend_FastAPI SHALL propagar el Correlation-ID de la solicitud de origen en dicha solicitud saliente.
3. WHEN el Sistema_Autenticacion registra un evento en logs, THE Sistema_Autenticacion SHALL emitir un registro estructurado que incluya el Correlation-ID y que excluya tokens, contraseñas, códigos TOTP y datos personales sensibles (correo, documento de identidad), enmascarando cualquier campo sensible mostrando como máximo los últimos 4 caracteres.
4. IF ocurre un error no controlado durante el procesamiento de una solicitud, THEN THE Backend_FastAPI SHALL responder al cliente con un mensaje genérico que no incluya detalles de implementación ni trazas de ejecución, SHALL registrar internamente el detalle del error junto con el Correlation-ID, y SHALL preservar el estado de los datos sin cambios parciales.
5. IF el Backend_FastAPI intenta comunicarse con un proveedor de identidad mediante un esquema distinto de HTTPS, THEN THE Backend_FastAPI SHALL rechazar la comunicación sin transmitir datos y SHALL registrar internamente el evento con el Correlation-ID.
6. WHEN el Backend_FastAPI recibe cualquier entrada del cliente, THE Backend_FastAPI SHALL validar en el servidor su tipo, formato, longitud y rango antes de procesarla, aplicando como mínimo: longitud máxima de 320 caracteres para cadenas de texto de identificación, exactamente 6 dígitos numéricos para códigos TOTP, y formato UUID v4 para identificadores.
7. IF una entrada recibida no cumple las reglas de validación de tipo, formato, longitud o rango, THEN THE Backend_FastAPI SHALL rechazar la solicitud sin procesarla, SHALL responder con un error genérico de validación que no revele la regla interna incumplida, y SHALL registrar internamente el detalle con el Correlation-ID.
