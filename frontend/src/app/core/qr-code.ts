/**
 * Generacion LOCAL del codigo QR de enrolamiento TOTP a partir de la URL
 * `otpauth://` (R8.4). Todo ocurre en el navegador: no hay llamadas de red ni
 * servicios externos de QR, de modo que el secreto TOTP nunca sale del cliente
 * (seguridad: no fuga de secretos). El resultado es un SVG en linea, compatible
 * con la CSP `script-src 'self'` (no inyecta scripts).
 *
 * El codificador QR es la libreria MIT de Project Nayuki, vendorizada en
 * `./vendor/qrcodegen.ts` (sin dependencia npm). Ver atribucion en ese archivo.
 */
import * as qrcodegenModule from './vendor/qrcodegen';

// El archivo vendorizado declara `export namespace qrcodegen { ... }`, por lo
// que al importarlo como modulo ES el namespace real queda anidado una vez.
const qrcodegen = qrcodegenModule.qrcodegen;

/** Nivel de correccion de errores: MEDIUM (~15%) equilibra densidad y robustez. */
const ECC = qrcodegen.QrCode.Ecc.MEDIUM;

/** Margen (modulos "quiet zone") recomendado por la especificacion QR. */
const BORDER_MODULES = 4;

/**
 * Convierte una URL `otpauth://` en un SVG de codigo QR escaneable, generado
 * localmente. El SVG es un string autocontenido (sin scripts) listo para
 * insertarse en el DOM tras sanitizarse.
 *
 * @param otpauthUrl URL `otpauth://totp/...` producida por el Client SDK.
 * @returns markup SVG del codigo QR (fondo claro, modulos oscuros).
 * @throws Error si la URL es vacia o no puede codificarse.
 */
export function buildTotpQrSvg(otpauthUrl: string): string {
  const data = (otpauthUrl ?? '').trim();
  if (data === '') {
    throw new Error('otpauth URL vacia');
  }

  const qr = qrcodegen.QrCode.encodeText(data, ECC);
  const size = qr.size;
  const dimension = size + BORDER_MODULES * 2;

  // Construir la ruta de los modulos oscuros como un unico path SVG.
  const parts: string[] = [];
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      if (qr.getModule(x, y)) {
        parts.push(`M${x + BORDER_MODULES},${y + BORDER_MODULES}h1v1h-1z`);
      }
    }
  }
  const path = parts.join(' ');

  // `shape-rendering=crispEdges` evita el antialias que dificulta el escaneo.
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${dimension} ${dimension}" ` +
    `width="220" height="220" shape-rendering="crispEdges" ` +
    `role="img" aria-label="Codigo QR para enrolar la verificacion en dos pasos">` +
    `<rect width="100%" height="100%" fill="#ffffff"/>` +
    `<path d="${path}" fill="#000000"/>` +
    `</svg>`
  );
}