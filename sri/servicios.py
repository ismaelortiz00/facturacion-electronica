"""
Flujo del comprobante electrónico:
  1. generar_y_firmar: arma el XML, lo firma y lo guarda (no necesita internet).
  2. procesar: lo envía al SRI (recepción) y consulta la autorización.
  3. Si se autoriza, envía al cliente el RIDE y el XML por correo (si tiene correo).
Si no hay internet, queda "Sin conexión" y se reintenta desde la pantalla de comprobantes
(el SRI da plazo para enviar comprobantes emitidos sin conexión).
"""
import logging
from datetime import datetime

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone
from lxml import etree

from facturacion.models import Empresa, ErrorNegocio as ErrorContable

from . import cliente, comprobantes, firma, ride
from .models import ComprobanteElectronico, FirmaElectronica

log = logging.getLogger(__name__)

TIPO = {'factura': '01', 'nota_credito': '04'}
GENERADORES = {'01': comprobantes.xml_factura, '04': comprobantes.xml_nota_credito}


def firma_de(empresa=None) -> FirmaElectronica | None:
    return FirmaElectronica.objects.order_by('id').first()


@transaction.atomic
def configurar_firma(empresa, contenido: bytes, clave: str, ambiente: str, enviar_correo=True) -> FirmaElectronica:
    try:
        _, cert = firma.cargar_p12(contenido, clave)
    except firma.ErrorFirma as error:
        raise ErrorContable(str(error))
    datos = firma.datos_certificado(cert)
    f = firma_de() or FirmaElectronica()
    f.archivo = contenido
    f.guardar_clave(clave)
    f.ambiente = ambiente if ambiente in ('1', '2') else '1'
    f.enviar_correo = enviar_correo
    f.titular, f.emisor, f.vence = datos['titular'][:250], datos['emisor'][:250], datos['vence']
    f.save()
    return f


def generar_y_firmar(documento) -> ComprobanteElectronico:
    """documento: factura o nota de crédito emitida. Si ya tiene comprobante electrónico, lo devuelve."""
    existente = ComprobanteElectronico.objects.filter(comprobante=documento).first()
    if existente:
        return existente
    empresa = Empresa.actual()
    f = firma_de()
    if f is None:
        raise ErrorContable('Falta la firma electrónica: súbela en Configuración → Firma electrónica.')
    if documento.estado != 'emitido':
        raise ErrorContable('Solo se envían al SRI documentos emitidos.')
    try:
        llave, cert = firma.cargar_p12(bytes(f.archivo), f.clave())
    except firma.ErrorFirma as error:
        raise ErrorContable(str(error))
    tipo = TIPO[documento.tipo]
    clave = comprobantes.clave_acceso(documento.fecha, tipo, empresa.ruc, f.ambiente, documento.establecimiento,
                                      documento.punto_emision, documento.secuencial)
    xml = firma.firmar(GENERADORES[tipo](documento, clave, f.ambiente), llave, cert)
    return ComprobanteElectronico.objects.create(tipo=tipo, comprobante=documento, clave_acceso=clave,
                                                 ambiente=f.ambiente, xml_firmado=xml.decode('utf-8'))


def procesar(comp: ComprobanteElectronico) -> ComprobanteElectronico:
    """Envía (si hace falta) y consulta la autorización. Nunca lanza error de red: lo registra."""
    if comp.estado in ('autorizado',):
        return comp
    comp.intentos += 1
    mensajes = []
    try:
        if comp.estado in ('firmado', 'error', 'devuelto'):
            estado, msgs = cliente.enviar(comp.xml_firmado.encode('utf-8'), comp.ambiente)
            mensajes += msgs
            # "CLAVE ACCESO REGISTRADA" (código 43) = ya se había recibido: seguimos a la autorización
            ya_registrada = any('43' in m.split('·')[0] for m in msgs)
            if estado == 'DEVUELTA' and not ya_registrada:
                comp.estado = 'devuelto'
                comp.mensajes = '\n'.join(mensajes)
                comp.save()
                return comp
            comp.estado = 'recibido'
        resultado = cliente.autorizar(comp.clave_acceso, comp.ambiente)
        mensajes += resultado['mensajes']
        if resultado['estado'] == 'AUTORIZADO':
            comp.estado = 'autorizado'
            comp.numero_autorizacion = resultado['numero'] or comp.clave_acceso
            comp.fecha_autorizacion = _fecha(resultado['fecha'])
            comp.xml_autorizado = xml_autorizado(comp, resultado)
        elif resultado['estado'] == 'NO AUTORIZADO':
            comp.estado = 'no_autorizado'
        # vacío o EN PROCESO: queda "recibido" y se vuelve a consultar
    except cliente.SinConexion as error:
        comp.estado = 'error' if comp.estado != 'recibido' else 'recibido'
        mensajes.append(str(error))
    comp.mensajes = '\n'.join(mensajes)
    comp.save()
    if comp.estado == 'autorizado':
        enviar_al_cliente(comp)
    return comp


def _fecha(texto):
    for formato in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%S.%f%z', '%d/%m/%Y %H:%M:%S'):
        try:
            f = datetime.strptime(texto.replace('-05:00', '-0500'), formato)
            return f if timezone.is_aware(f) else timezone.make_aware(f)
        except ValueError:
            continue
    return timezone.now()


def xml_autorizado(comp, resultado) -> str:
    """El XML que se entrega al cliente: la autorización con el comprobante firmado adentro."""
    a = etree.Element('autorizacion')
    for nombre, valor in (('estado', 'AUTORIZADO'), ('numeroAutorizacion', comp.numero_autorizacion),
                          ('fechaAutorizacion', resultado.get('fecha') or timezone.now().isoformat()),
                          ('ambiente', 'PRODUCCIÓN' if comp.ambiente == '2' else 'PRUEBAS')):
        etree.SubElement(a, nombre).text = valor
    etree.SubElement(a, 'comprobante').text = etree.CDATA(resultado.get('comprobante') or comp.xml_firmado)
    return etree.tostring(a, xml_declaration=True, encoding='UTF-8').decode('utf-8')


def enviar_al_cliente(comp: ComprobanteElectronico) -> bool:
    f = firma_de()
    destinatario = comp.comprobante.cliente.correo
    if comp.correo_enviado or not destinatario or (f and not f.enviar_correo):
        return False
    doc = comp.documento
    try:
        correo = EmailMessage(
            subject=f'{comp.get_tipo_display()} {doc.numero} · {comp.empresa}',
            body=(f'Estimado cliente:\n\nAdjuntamos su {comp.get_tipo_display().lower()} electrónica n.º {doc.numero}, '
                  f'autorizada por el SRI con el número {comp.numero_autorizacion}.\n\nGracias por su visita.\n'
                  f'{comp.empresa}'),
            from_email=settings.DEFAULT_FROM_EMAIL, to=[destinatario])
        correo.attach(f'{comp.clave_acceso}.pdf', ride.generar(comp), 'application/pdf')
        correo.attach(f'{comp.clave_acceso}.xml', comp.xml_autorizado or comp.xml_firmado, 'application/xml')
        correo.send()
        comp.correo_enviado = True
        comp.save(update_fields=['correo_enviado'])
        return True
    except Exception as error:  # el correo nunca debe impedir la autorización
        log.warning('No se pudo enviar el comprobante %s: %s', comp.clave_acceso, error)
        return False


def emitir_electronico(documento):
    """Atajo para las pantallas: firma y procesa. Devuelve (comprobante | None, mensaje)."""
    if firma_de() is None:
        return None, f'{documento.get_tipo_display()} emitida. Para enviarla al SRI falta subir la firma electrónica.'
    try:
        comp = generar_y_firmar(documento)
    except ErrorContable as error:
        return None, error.message
    comp = procesar(comp)
    return comp, f'SRI: {comp.get_estado_display().lower()}.'
