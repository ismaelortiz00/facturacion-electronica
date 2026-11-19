"""
Vistas: login, registro, verificación de correo, reenvío, panel y salida.
Las acciones de formulario responden JSON para que la página no se recargue.
"""
import re
import secrets
import time
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from . import captcha
from .models import RegistroAcceso, Usuario
from .servicios import (avisar_bloqueo, enviar_verificacion, fallos_ip_recientes, hash_codigo,
                        huella_navegador, registrar_fallo_ip, registrar_log)
from .validadores import revisar_cedula, revisar_correo

AVISOS = {
    'expirada': 'Tu sesión se cerró por inactividad. Ingresa de nuevo.',
    'salida': 'Cerraste sesión correctamente.',
    'verificado': 'Correo verificado. Ya puedes iniciar sesión.',
}


def datos_codigo(correo, codigo):
    """Datos para que la página muestre el paso 'Ingresa el código'."""
    datos = {'verificar': True, 'correo': correo}
    if settings.MOSTRAR_CODIGO_EN_PANTALLA and codigo:
        datos['codigo_desarrollo'] = codigo  # solo en desarrollo, sin servidor de correo
    return datos


def responder(request, exito, mensaje, **extra):
    """Respuesta JSON. Siempre entrega un token 2 nuevo y pide reiniciar el captcha."""
    return JsonResponse({'success': exito, 'message': mensaje, 'reiniciar_captcha': True,
                         'token2': captcha.emitir_token2(request.session), **extra})


def fallo(request, mensaje, **extra):
    return responder(request, False, mensaje, **extra)


# =====================================================================
#  LOGIN
# =====================================================================
@never_cache
@require_GET
def login_vista(request):
    if request.user.is_authenticated:
        return redirect('panel')
    return render(request, 'cuentas/login.html', {
        **captcha.contexto(request),
        'aviso': AVISOS.get(request.GET.get('m', '')),
        'max_intentos': settings.MAX_INTENTOS,
        'minutos_bloqueo': settings.MINUTOS_BLOQUEO,
    })


@require_POST
def validar_login(request):
    """
    Orden de controles:
     1. CSRF (lo hace Django)   2. Límite por IP   3. Captcha (token 1 + token 2)
     4. Formato cédula/correo   5. Usuario existe y activo   6. Bloqueo vigente
     7. Contraseña + conteo de intentos   8. Correo verificado   9. Sesión nueva
    """
    # --- 2. Límite de fallos por IP -----------------------------------
    if fallos_ip_recientes(request) >= settings.MAX_INTENTOS_IP:
        registrar_log(request, 'LOGIN', '', 'BLOQUEADO', 'Límite de intentos por IP')
        return fallo(request, f'Demasiados intentos desde tu conexión. Espera {settings.MINUTOS_BLOQUEO} minutos.')

    # --- 3. Captcha ---------------------------------------------------
    ok, mensaje = captcha.consumir(request)
    if not ok:
        registrar_log(request, 'LOGIN', '', 'BLOQUEADO', 'Captcha: ' + mensaje)
        return fallo(request, mensaje)

    # --- 4. Datos -----------------------------------------------------
    dato = request.POST.get('usuario', '').strip()
    clave = request.POST.get('clave', '')
    if not dato or not clave:
        return fallo(request, 'Ingresa tu cédula o correo y tu contraseña.')

    if '@' in dato:
        filtro = Q(correo=dato.lower())
    else:
        ok, mensaje = revisar_cedula(dato)
        if not ok:
            registrar_fallo_ip(request)
            registrar_log(request, 'LOGIN', dato, 'FALLIDO', 'Cédula inválida')
            return fallo(request, mensaje)
        filtro = Q(cedula=dato)

    with transaction.atomic():
        # select_for_update bloquea la fila: dos intentos simultáneos no se pisan
        usuario = Usuario.objects.select_for_update().filter(filtro).first()

        # --- 5. Usuario ---------------------------------------------------
        if usuario is None:
            Usuario().set_password(clave)  # mismo tiempo de respuesta: no revela si existe
            registrar_fallo_ip(request)
            registrar_log(request, 'LOGIN', dato, 'FALLIDO', 'Usuario no existe')
            return fallo(request, 'Usuario o contraseña incorrectos.')

        if not usuario.is_active:
            registrar_log(request, 'LOGIN', usuario.correo, 'BLOQUEADO', 'Cuenta deshabilitada')
            return fallo(request, 'Tu cuenta está deshabilitada. Contacta al administrador.')

        # --- 6. Bloqueo ---------------------------------------------------
        segundos = usuario.segundos_bloqueo
        if segundos:
            registrar_log(request, 'LOGIN', usuario.correo, 'BLOQUEADO', 'Intento durante el bloqueo')
            return fallo(request, f'Cuenta bloqueada por intentos fallidos. Inténtalo en {-(-segundos // 60)} minuto(s).',
                         segundos_bloqueo=segundos)
        if usuario.bloqueado_hasta:  # el bloqueo ya venció: se reinicia el conteo
            usuario.intentos_fallidos, usuario.bloqueado_hasta = 0, None

        # --- 7. Contraseña (Argon2) ---------------------------------------
        if not usuario.check_password(clave):
            usuario.intentos_fallidos += 1
            restantes = settings.MAX_INTENTOS - usuario.intentos_fallidos
            registrar_fallo_ip(request)

            if restantes <= 0:
                usuario.bloqueado_hasta = timezone.now() + timedelta(minutes=settings.MINUTOS_BLOQUEO)
                usuario.save(update_fields=['intentos_fallidos', 'bloqueado_hasta'])
                registrar_log(request, 'LOGIN', usuario.correo, 'BLOQUEADO',
                              f'Cuenta bloqueada tras {usuario.intentos_fallidos} intentos')
                transaction.on_commit(lambda: avisar_bloqueo(usuario))
                return fallo(request, f'Contraseña incorrecta. Tu cuenta quedó bloqueada {settings.MINUTOS_BLOQUEO} minutos.',
                             segundos_bloqueo=settings.MINUTOS_BLOQUEO * 60, intentos_restantes=0)

            usuario.save(update_fields=['intentos_fallidos', 'bloqueado_hasta'])
            registrar_log(request, 'LOGIN', usuario.correo, 'FALLIDO',
                          f'Contraseña incorrecta ({usuario.intentos_fallidos}/{settings.MAX_INTENTOS})')
            return fallo(request, f'Usuario o contraseña incorrectos. Te queda(n) {restantes} intento(s).',
                         intentos_restantes=restantes)

        # Contraseña correcta: se reinicia el conteo
        usuario.intentos_fallidos, usuario.bloqueado_hasta = 0, None
        usuario.save(update_fields=['intentos_fallidos', 'bloqueado_hasta'])

    # --- 8. Correo verificado -------------------------------------------
    if not usuario.correo_verificado:
        registrar_log(request, 'LOGIN', usuario.correo, 'BLOQUEADO', 'Correo no verificado')
        return fallo(request, 'Tu correo todavía no está verificado. Entra a "Verificar mi correo" para recibir el código.',
                     reenviar=True)

    # --- 9. Acceso: login() crea un ID de sesión nuevo (evita fijación) --
    login(request, usuario)
    request.session['huella'] = huella_navegador(request)
    request.session['ultimo_movimiento'] = time.time()
    registrar_log(request, 'LOGIN', usuario.correo, 'OK', 'Inicio de sesión correcto')
    return JsonResponse({'success': True, 'message': f'Bienvenido(a), {usuario.nombres}', 'redirect': '/panel/'})


# =====================================================================
#  REGISTRO
# =====================================================================
@never_cache
@require_GET
def registro_vista(request):
    return render(request, 'cuentas/registro.html', captcha.contexto(request))


@require_POST
def procesar_registro(request):
    ok, mensaje = captcha.consumir(request)
    if not ok:
        return fallo(request, mensaje)

    cedula = request.POST.get('cedula', '').strip()
    nombres = re.sub(r'\s+', ' ', request.POST.get('nombres', '')).strip()
    clave = request.POST.get('clave', '')
    confirmar = request.POST.get('confirmar', '')

    ok, mensaje = revisar_cedula(cedula)
    if not ok:
        return fallo(request, mensaje)

    if not (5 <= len(nombres) <= 120) or not re.fullmatch(r"[^\W\d_]+(?:[ .'-][^\W\d_]+)*\.?", nombres):
        return fallo(request, 'Ingresa tus nombres y apellidos (solo letras).')

    ok, mensaje, correo = revisar_correo(request.POST.get('correo', ''))
    if not ok:
        return fallo(request, mensaje)

    if clave != confirmar:
        return fallo(request, 'Las contraseñas no coinciden.')
    try:
        validate_password(clave, Usuario(cedula=cedula, correo=correo, nombres=nombres))
    except ValidationError as error:
        return fallo(request, ' '.join(error.messages))

    existente = Usuario.objects.filter(Q(cedula=cedula) | Q(correo=correo)).first()
    if existente:
        registrar_log(request, 'REGISTRO', correo, 'FALLIDO', 'Cédula o correo ya registrado')
        return fallo(request, 'Esta cédula ya tiene una cuenta registrada.' if existente.cedula == cedula
                     else 'Este correo ya tiene una cuenta registrada.')

    try:
        with transaction.atomic():
            usuario = Usuario.objects.create_user(cedula, correo, nombres, clave)  # clave encriptada
    except (IntegrityError, ValidationError):
        registrar_log(request, 'REGISTRO', correo, 'ERROR', 'No se pudo crear el usuario')
        return fallo(request, 'No se pudo crear la cuenta. Inténtalo de nuevo.')

    enviado, codigo = enviar_verificacion(request, usuario)
    if not enviado:
        registrar_log(request, 'REGISTRO', correo, 'ERROR', 'Cuenta creada, pero falló el envío del código')
        return responder(request, True, 'Cuenta creada, pero no pudimos enviar el código. Presiona "Reenviar código".',
                         **datos_codigo(correo, None))

    registrar_log(request, 'REGISTRO', correo, 'OK', 'Cuenta creada, código enviado')
    return responder(request, True, f'Cuenta creada. Escribe el código de 6 dígitos que enviamos a {correo}.',
                     **datos_codigo(correo, codigo))


# =====================================================================
#  VERIFICACIÓN DE CORREO CON CÓDIGO DE 6 DÍGITOS
# =====================================================================
def _limite_reenvios(correo) -> bool:
    desde = timezone.now() - timedelta(minutes=settings.MINUTOS_BLOQUEO)
    return RegistroAcceso.objects.filter(accion='REENVIO_CODIGO', usuario=correo, fecha__gte=desde).count() >= 3


def _enviar_codigo_si_existe(request, correo):
    """Envía un código nuevo si la cuenta existe y no está verificada. Devuelve el código (o None)."""
    usuario = Usuario.objects.filter(correo=correo, correo_verificado=False, is_active=True).first()
    registrar_log(request, 'REENVIO_CODIGO', correo, 'OK' if usuario else 'IGNORADO',
                  'Código reenviado' if usuario else 'Sin cuenta pendiente')
    if usuario:
        enviado, codigo = enviar_verificacion(request, usuario)
        return codigo if enviado else None
    return None


@never_cache
def reenviar_verificacion(request):
    """Página 'Verificar mi correo': pide el correo (con captcha) y luego el código."""
    if request.method != 'POST':
        return render(request, 'cuentas/reenviar.html', captcha.contexto(request))

    ok, mensaje = captcha.consumir(request)
    if not ok:
        return fallo(request, mensaje)

    correo = request.POST.get('correo', '').strip().lower()
    if '@' not in correo:
        return fallo(request, 'El correo electrónico no tiene un formato válido.')
    if _limite_reenvios(correo):
        return fallo(request, f'Ya pediste varios códigos. Espera {settings.MINUTOS_BLOQUEO} minutos.')

    codigo = _enviar_codigo_si_existe(request, correo)
    # Misma respuesta exista o no la cuenta: no revela qué correos están registrados
    return responder(request, True, 'Si el correo está registrado y sin verificar, te enviamos un código de 6 dígitos.',
                     **datos_codigo(correo, codigo))


@require_POST
def reenviar_codigo(request):
    """Botón 'Reenviar código' dentro del paso del código."""
    correo = request.POST.get('correo', '').strip().lower()
    if '@' not in correo:
        return JsonResponse({'success': False, 'message': 'Correo no válido.'})
    if _limite_reenvios(correo):
        return JsonResponse({'success': False,
                             'message': f'Ya pediste varios códigos. Espera {settings.MINUTOS_BLOQUEO} minutos.'})
    codigo = _enviar_codigo_si_existe(request, correo)
    return JsonResponse({'success': True, 'message': 'Te enviamos un código nuevo.', **datos_codigo(correo, codigo)})


@require_POST
def verificar_codigo(request):
    correo = request.POST.get('correo', '').strip().lower()
    codigo = re.sub(r'\D', '', request.POST.get('codigo', ''))

    def error(mensaje):
        return JsonResponse({'success': False, 'message': mensaje})

    if len(codigo) != 6:
        return error('Escribe los 6 dígitos del código.')

    with transaction.atomic():
        usuario = Usuario.objects.select_for_update().filter(correo=correo, correo_verificado=False).first()
        if (usuario is None or not usuario.token_verificacion
                or not usuario.expira_verificacion or usuario.expira_verificacion < timezone.now()):
            return error('El código venció o no es válido. Presiona "Reenviar código".')

        if usuario.intentos_codigo >= settings.MAX_INTENTOS_CODIGO:
            return error('Demasiados intentos. Presiona "Reenviar código" para recibir uno nuevo.')

        if not secrets.compare_digest(hash_codigo(usuario, codigo), usuario.token_verificacion):
            usuario.intentos_codigo += 1
            usuario.save(update_fields=['intentos_codigo'])
            restantes = settings.MAX_INTENTOS_CODIGO - usuario.intentos_codigo
            registrar_log(request, 'VERIFICAR_CORREO', correo, 'FALLIDO', f'Código incorrecto ({usuario.intentos_codigo})')
            return error(f'Código incorrecto. Te queda(n) {restantes} intento(s).')

        usuario.correo_verificado = True
        usuario.token_verificacion = None
        usuario.expira_verificacion = None
        usuario.intentos_codigo = 0
        usuario.save(update_fields=['correo_verificado', 'token_verificacion', 'expira_verificacion', 'intentos_codigo'])

    registrar_log(request, 'VERIFICAR_CORREO', correo, 'OK', 'Correo verificado con código')
    return JsonResponse({'success': True, 'message': 'Correo verificado. Ya puedes iniciar sesión.',
                         'redirect': '/?m=verificado'})


# =====================================================================
#  PANEL Y SALIDA
# =====================================================================
@never_cache
@login_required
def panel(request):
    return redirect('facturacion:inicio')


@require_POST
def salir(request):
    if request.user.is_authenticated:
        registrar_log(request, 'LOGOUT', request.user.correo, 'OK', 'Cierre de sesión')
    logout(request)
    return redirect('/?m=salida')
