"""
CAPTCHA CON DOS TOKENS
Token 1: hCaptcha o Google reCAPTCHA v2. El usuario marca "No soy un robot"
         y, cuando el servicio lo pide, selecciona imágenes REALES (autos,
         semáforos, bicicletas...). El servidor confirma el token con el
         proveedor (siteverify); cada token sirve una sola vez.
Token 2: token propio del servidor guardado en la sesión. Es de un solo uso,
         vence, exige un tiempo humano mínimo y que el campo trampa
         (honeypot) llegue vacío.
"""
import json
import logging
import secrets
import time
import urllib.parse
import urllib.request

from django.conf import settings

log = logging.getLogger(__name__)

PROVEEDORES = {
    'hcaptcha': {
        'script': 'https://js.hcaptcha.com/1/api.js?hl=es',
        'clase': 'h-captcha',
        'campo': 'h-captcha-response',
        'verificar': 'https://api.hcaptcha.com/siteverify',
        'js': 'hcaptcha',
        'csp': 'https://hcaptcha.com https://*.hcaptcha.com',
    },
    'recaptcha': {
        'script': 'https://www.google.com/recaptcha/api.js?hl=es',
        'clase': 'g-recaptcha',
        'campo': 'g-recaptcha-response',
        'verificar': 'https://www.google.com/recaptcha/api/siteverify',
        'js': 'grecaptcha',
        'csp': 'https://www.google.com https://www.gstatic.com https://recaptcha.google.com',
    },
}


def proveedor() -> dict:
    return PROVEEDORES[settings.CAPTCHA_PROVEEDOR]


# ---------------------------------------------------------------------
#  Token 2: token del servidor
# ---------------------------------------------------------------------
def emitir_token2(session) -> str:
    token = secrets.token_hex(32)
    session['token2'] = {'valor': token, 'emitido': time.time()}
    return token


def contexto(request) -> dict:
    """Datos que necesita la plantilla _captcha.html."""
    p = proveedor()
    return {
        'token2': emitir_token2(request.session),
        'captcha_script': p['script'],
        'captcha_clase': p['clase'],
        'captcha_js': p['js'],
        'captcha_sitekey': settings.CAPTCHA_SITE_KEY,
    }


def _validar_token2(request) -> tuple[bool, str]:
    guardado = request.session.pop('token2', None)  # un solo uso
    enviado = request.POST.get('token2', '')
    if not guardado or not isinstance(enviado, str) or not secrets.compare_digest(guardado['valor'], enviado):
        return False, 'La página expiró. Recárgala e inténtalo de nuevo.'
    if request.POST.get('website'):  # campo trampa: solo lo llenan los bots
        return False, 'No se pudo completar la verificación.'
    transcurrido = time.time() - guardado['emitido']
    if transcurrido < settings.CAPTCHA_TIEMPO_MINIMO:
        return False, 'Demasiado rápido. Espera un momento e inténtalo otra vez.'
    if transcurrido > settings.CAPTCHA_VIGENCIA:
        return False, 'La página expiró. Recárgala e inténtalo de nuevo.'
    return True, ''


# ---------------------------------------------------------------------
#  Token 1: verificación con hCaptcha / reCAPTCHA
# ---------------------------------------------------------------------
def verificar_con_proveedor(respuesta: str, ip: str) -> bool:
    if not respuesta:
        return False
    p = proveedor()
    datos = {'secret': settings.CAPTCHA_SECRET_KEY, 'response': respuesta, 'remoteip': ip}
    if settings.CAPTCHA_PROVEEDOR == 'hcaptcha':
        datos['sitekey'] = settings.CAPTCHA_SITE_KEY
    try:
        peticion = urllib.request.Request(p['verificar'], data=urllib.parse.urlencode(datos).encode(), method='POST')
        with urllib.request.urlopen(peticion, timeout=8) as r:
            resultado = json.loads(r.read().decode())
    except Exception as error:
        log.error('No se pudo verificar el captcha: %s', error)
        return False
    if not resultado.get('success'):
        log.warning('Captcha rechazado: %s', resultado.get('error-codes'))
    return bool(resultado.get('success'))


def consumir(request) -> tuple[bool, str]:
    """Valida los dos tokens. Primero el propio (no cuesta nada) y luego el del proveedor."""
    ok, mensaje = _validar_token2(request)
    if not ok:
        return False, mensaje
    respuesta = request.POST.get(proveedor()['campo'], '')
    if not verificar_con_proveedor(respuesta, request.META.get('REMOTE_ADDR', '')):
        return False, 'Marca la casilla "No soy un robot" y completa la selección de imágenes.'
    return True, ''
