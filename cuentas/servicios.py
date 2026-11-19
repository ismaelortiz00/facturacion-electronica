"""
Servicios comunes: IP del cliente, bitácora, intentos por IP, huella del
navegador y envío del correo de verificación.
"""
import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone

from .models import IntentoIP, RegistroAcceso


def obtener_ip(request) -> str:
    # Solo REMOTE_ADDR: X-Forwarded-For lo puede falsificar el cliente.
    # Si usas un proxy inverso de confianza (Nginx), ajusta esta función.
    return request.META.get('REMOTE_ADDR', '0.0.0.0')


def registrar_log(request, accion, usuario, estado, detalle=''):
    RegistroAcceso.objects.create(
        accion=accion, usuario=(usuario or '')[:150], ip=obtener_ip(request),
        estado=estado, detalle=(detalle or '')[:500],
    )


def registrar_fallo_ip(request):
    IntentoIP.objects.create(ip=obtener_ip(request))


def fallos_ip_recientes(request) -> int:
    desde = timezone.now() - timedelta(minutes=settings.MINUTOS_BLOQUEO)
    return IntentoIP.objects.filter(ip=obtener_ip(request), fecha__gte=desde).count()


def huella_navegador(request) -> str:
    datos = request.META.get('HTTP_USER_AGENT', '') + '|' + request.META.get('HTTP_ACCEPT_LANGUAGE', '')
    return hashlib.sha256(datos.encode()).hexdigest()


def enviar_correo(asunto, destinatario, plantilla, contexto) -> bool:
    html = render_to_string(plantilla, contexto)
    texto = contexto.get('texto_plano', asunto)
    try:
        send_mail(asunto, texto, None, [destinatario], html_message=html)
        return True
    except Exception as error:  # el registro no debe caerse si falla el servidor de correo
        import logging
        logging.getLogger(__name__).error('Error al enviar correo: %s', error)
        return False


def hash_codigo(usuario, codigo: str) -> str:
    """Hash del código ligado al usuario y a la clave secreta: en la BD nunca queda el código."""
    return hashlib.sha256(f'{usuario.pk}:{codigo}:{settings.SECRET_KEY}'.encode()).hexdigest()


def enviar_verificacion(request, usuario) -> tuple[bool, str]:
    """Genera un código de 6 dígitos, guarda solo su hash y lo envía por correo."""
    codigo = f'{secrets.randbelow(10 ** 6):06d}'
    usuario.token_verificacion = hash_codigo(usuario, codigo)
    usuario.expira_verificacion = timezone.now() + timedelta(minutes=settings.MINUTOS_CODIGO_CORREO)
    usuario.intentos_codigo = 0
    usuario.save(update_fields=['token_verificacion', 'expira_verificacion', 'intentos_codigo'])

    enviado = enviar_correo(
        f'{codigo} es tu código de verificación - Facturación Turística IK', usuario.correo, 'correos/verificacion.html',
        {'usuario': usuario, 'codigo': codigo, 'minutos': settings.MINUTOS_CODIGO_CORREO,
         'texto_plano': f'Tu código de verificación es: {codigo}'},
    )
    return enviado, codigo


def avisar_bloqueo(usuario):
    enviar_correo(
        'Alerta de seguridad - Facturación Turística IK', usuario.correo, 'correos/bloqueo.html',
        {'usuario': usuario, 'intentos': settings.MAX_INTENTOS, 'minutos': settings.MINUTOS_BLOQUEO,
         'texto_plano': 'Tu cuenta fue bloqueada temporalmente por intentos fallidos.'},
    )
