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
    tenantId: 'poc-auth-2fa-vh5h3',
    // Dominio corporativo `hd` del camino B (login con Google). Cosmetico: filtra
    // el selector de cuenta de Google. La autorizacion real de dominio es
    // server-side (CORPORATE_ALLOWED_DOMAINS, POCA-004). Ejemplo de desarrollo local.
    corporateHostedDomain: 'segurosbolivar.com'
  }
};
