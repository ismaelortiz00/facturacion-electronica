"""
COMPROBANTES ELECTRÓNICOS DEL SRI
Firma electrónica de la empresa (.p12, clave cifrada) y el seguimiento de cada comprobante:
firmado → recibido → autorizado (o devuelto / no autorizado / sin conexión).
"""
import base64
import hashlib

from django.conf import settings
from django.db import models


def _fernet():
    from cryptography.fernet import Fernet
    clave = hashlib.sha256(('firma-sri|' + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(clave))


class FirmaElectronica(models.Model):
    AMBIENTES = [('1', 'Pruebas'), ('2', 'Producción')]
    archivo = models.BinaryField('archivo .p12')
    clave_cifrada = models.BinaryField()
    titular = models.CharField(max_length=250, blank=True)
    emisor = models.CharField(max_length=250, blank=True)
    vence = models.DateTimeField(null=True, blank=True)
    ambiente = models.CharField(max_length=1, choices=AMBIENTES, default='1')
    enviar_correo = models.BooleanField('enviar el PDF y el XML al cliente al autorizarse', default=True)
    actualizada = models.DateTimeField(auto_now=True)

    def guardar_clave(self, clave: str):
        self.clave_cifrada = _fernet().encrypt(clave.encode())

    def clave(self) -> str:
        return _fernet().decrypt(bytes(self.clave_cifrada)).decode()


class ComprobanteElectronico(models.Model):
    TIPOS = [('01', 'Factura'), ('04', 'Nota de crédito')]
    ESTADOS = [('firmado', 'Firmado, por enviar'), ('recibido', 'Recibido por el SRI'),
               ('devuelto', 'Devuelto por el SRI'), ('autorizado', 'Autorizado'),
               ('no_autorizado', 'No autorizado'), ('error', 'Sin conexión con el SRI')]
    tipo = models.CharField(max_length=2, choices=TIPOS)
    comprobante = models.OneToOneField('facturacion.Comprobante', models.PROTECT, related_name='electronico')
    clave_acceso = models.CharField(max_length=49, unique=True)
    ambiente = models.CharField(max_length=1)
    xml_firmado = models.TextField()
    xml_autorizado = models.TextField(blank=True)
    estado = models.CharField(max_length=13, choices=ESTADOS, default='firmado')
    numero_autorizacion = models.CharField(max_length=49, blank=True)
    fecha_autorizacion = models.DateTimeField(null=True, blank=True)
    mensajes = models.TextField(blank=True)
    intentos = models.PositiveIntegerField(default=0)
    correo_enviado = models.BooleanField(default=False)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-creado']

    def __str__(self):
        return f'{self.get_tipo_display()} {self.clave_acceso}'

    @property
    def documento(self):
        return self.comprobante

    @property
    def empresa(self):
        from facturacion.models import Empresa
        return Empresa.actual()
