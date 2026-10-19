// CONFIG LOCAL DE DESARROLLO (no para produccion).
// apiBaseUrl relativo: el proxy de ng serve reenvia /api al backend (localhost:8081),
// de modo que el navegador ve todo en el MISMO origen (localhost:4200) y la cookie de sesion fluye.
window.__APP_CONFIG__ = {
  apiBaseUrl: '/api/v1',
  firebase: {
    apiKey: 'AIzaSyCjpoFIT04KLhVop0EmBPLyOCvROMiRhMc',
    authDomain: 'sandbox-arquitectura.firebaseapp.com',
    projectId: 'sandbox-arquitectura',
    samlProviderId: 'saml.f5apm',
    tenantId: 'poc-auth-2fa-vh5h3'
  }
};
