"""
Middleware de seguridad:
- SesionSeguraMiddleware: cierra la sesión por inactividad o si cambia el navegador.
- CabecerasSeguridadMiddleware: agrega la política de contenido (CSP).
"""
import time

from django.conf import settings
from django.contrib.auth import logout
from django.shortcuts import redirect

from .captcha import proveedor
from .servicios import huella_navegador, registrar_log


class SesionSeguraMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            ahora = time.time()
            ultimo = request.session.get('ultimo_movimiento', ahora)
            inactivo = ahora - ultimo > settings.MINUTOS_SESION * 60
            otra_huella = request.session.get('huella') != huella_navegador(request)

            if inactivo or otra_huella:
                registrar_log(request, 'SESION', request.user.correo, 'EXPIRADA',
                              'Inactividad' if inactivo else 'Cambio de navegador')
                logout(request)
                return redirect('/?m=expirada')
            request.session['ultimo_movimiento'] = ahora

        return self.get_response(request)


class CabecerasSeguridadMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        respuesta = self.get_response(request)
        if not request.path.startswith('/admin/'):
            ext = proveedor()['csp']  # dominios de hCaptcha o reCAPTCHA
            respuesta.setdefault(
                'Content-Security-Policy',
                f"default-src 'self'; img-src 'self' data: {ext}; style-src 'self' 'unsafe-inline' {ext}; "
                f"script-src 'self' {ext}; frame-src {ext}; connect-src 'self' {ext}; "
                "form-action 'self'; frame-ancestors 'none'",
            )
        return respuesta
