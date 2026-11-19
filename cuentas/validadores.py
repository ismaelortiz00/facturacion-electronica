"""
Validaciones: cédula ecuatoriana, correo electrónico y contraseña segura.
"""
import re
import socket

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email


# ---------------------------------------------------------------------
#  CÉDULA ECUATORIANA (algoritmo módulo 10)
#  1. 10 dígitos numéricos.
#  2. Dos primeros dígitos = provincia: 01 a 24, o 30 (ecuatorianos en el exterior).
#  3. Tercer dígito entre 0 y 5 (persona natural).
#  4. Los 9 primeros dígitos se multiplican por 2,1,2,1,2,1,2,1,2;
#     si un producto es mayor que 9 se le resta 9; se suman todos.
#  5. Verificador = (10 - (suma % 10)) % 10 y debe ser igual al 10.º dígito.
#  Esto confirma que el número está bien formado. A quién pertenece
#  solo lo confirma el Registro Civil o el SRI.
# ---------------------------------------------------------------------
def revisar_cedula(cedula: str) -> tuple[bool, str]:
    if not re.fullmatch(r'\d{10}', cedula or ''):
        return False, 'La cédula debe tener exactamente 10 números.'

    provincia = int(cedula[:2])
    if not (1 <= provincia <= 24 or provincia == 30):
        return False, 'Los dos primeros dígitos de la cédula no corresponden a una provincia válida.'

    if int(cedula[2]) > 5:
        return False, 'El tercer dígito de la cédula debe estar entre 0 y 5.'

    coeficientes = (2, 1, 2, 1, 2, 1, 2, 1, 2)
    suma = 0
    for digito, coef in zip(cedula[:9], coeficientes):
        producto = int(digito) * coef
        suma += producto - 9 if producto > 9 else producto

    verificador = (10 - suma % 10) % 10
    if verificador != int(cedula[9]):
        return False, 'La cédula no es válida (el dígito verificador no coincide).'
    return True, 'Cédula válida.'


def validar_cedula(valor):
    """Validador para el campo `cedula` del modelo."""
    ok, mensaje = revisar_cedula(valor)
    if not ok:
        raise ValidationError(mensaje, code='cedula_invalida')


# ---------------------------------------------------------------------
#  CORREO: formato + dominio que realmente recibe correo (registro MX)
# ---------------------------------------------------------------------
def dominio_recibe_correo(dominio: str) -> bool:
    try:
        import dns.resolver  # paquete dnspython
        try:
            return bool(dns.resolver.resolve(dominio, 'MX', lifetime=4))
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
            pass
        except dns.exception.Timeout:
            return True  # si el DNS no responde, no se bloquea al usuario
    except ImportError:
        pass
    try:
        socket.gethostbyname(dominio)  # sin MX, al menos debe existir el dominio
        return True
    except OSError:
        return False


def revisar_correo(correo: str) -> tuple[bool, str, str]:
    correo = (correo or '').strip().lower()
    try:
        validate_email(correo)
    except ValidationError:
        return False, 'El correo electrónico no tiene un formato válido.', correo
    if len(correo) > 150:
        return False, 'El correo electrónico es demasiado largo.', correo
    if settings.VERIFICAR_DOMINIO_CORREO and not dominio_recibe_correo(correo.rsplit('@', 1)[1]):
        return False, 'El dominio del correo no existe o no recibe correos.', correo
    return True, 'Correo válido.', correo


# ---------------------------------------------------------------------
#  CONTRASEÑA SEGURA (se registra en AUTH_PASSWORD_VALIDATORS)
# ---------------------------------------------------------------------
class ClaveSeguraValidator:
    reglas = [
        (lambda c: len(c) >= 8, 'mínimo 8 caracteres'),
        (lambda c: len(c) <= 128, 'máximo 128 caracteres'),
        (lambda c: re.search(r'[A-Z]', c), 'una mayúscula'),
        (lambda c: re.search(r'[a-z]', c), 'una minúscula'),
        (lambda c: re.search(r'\d', c), 'un número'),
        (lambda c: re.search(r'[^A-Za-z0-9]', c), 'un símbolo (ej: ! @ # $ %)'),
    ]

    def validate(self, password, user=None):
        faltan = [texto for regla, texto in self.reglas if not regla(password)]
        if faltan:
            raise ValidationError('La contraseña necesita: ' + ', '.join(faltan) + '.', code='clave_debil')

    def get_help_text(self):
        return 'Mínimo 8 caracteres, con mayúscula, minúscula, número y símbolo.'
