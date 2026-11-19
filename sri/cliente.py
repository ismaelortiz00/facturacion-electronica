"""
Conexión con los servicios web offline del SRI (recepción y autorización).
Ambiente 1 = pruebas (celcer.sri.gob.ec) · 2 = producción (cel.sri.gob.ec).
"""
import base64
import urllib.error
import urllib.request

from lxml import etree

URLS = {
    '1': 'https://celcer.sri.gob.ec/comprobantes-electronicos-ws/',
    '2': 'https://cel.sri.gob.ec/comprobantes-electronicos-ws/',
}
TIEMPO = 25  # segundos


class SinConexion(Exception):
    pass


def _post(url: str, cuerpo: str) -> etree._Element:
    pedido = urllib.request.Request(url, data=cuerpo.encode('utf-8'), method='POST',
                                    headers={'Content-Type': 'text/xml; charset=utf-8', 'SOAPAction': ''})
    try:
        with urllib.request.urlopen(pedido, timeout=TIEMPO) as r:
            return etree.fromstring(r.read())
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise SinConexion(f'No se pudo conectar con el SRI: {error}')
    except etree.XMLSyntaxError:
        raise SinConexion('El SRI respondió algo que no es XML (el servicio puede estar en mantenimiento).')


def _mensajes(nodo) -> list[str]:
    salida = []
    for m in nodo.iter('mensaje'):
        if m.find('identificador') is None:
            continue
        partes = [m.findtext('identificador', ''), m.findtext('mensaje', ''), m.findtext('informacionAdicional', '')]
        salida.append(f"[{m.findtext('tipo', '')}] " + ' · '.join(p for p in partes if p))
    return salida


def enviar(xml_firmado: bytes, ambiente: str) -> tuple[str, list[str]]:
    """Recepción. Devuelve ('RECIBIDA' | 'DEVUELTA', mensajes)."""
    cuerpo = ('<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
              'xmlns:ec="http://ec.gob.sri.ws.recepcion"><soapenv:Header/><soapenv:Body>'
              f'<ec:validarComprobante><xml>{base64.b64encode(xml_firmado).decode()}</xml></ec:validarComprobante>'
              '</soapenv:Body></soapenv:Envelope>')
    respuesta = _post(URLS[ambiente] + 'RecepcionComprobantesOffline', cuerpo)
    estado = next((n.text for n in respuesta.iter('estado')), '') or ''
    return estado.strip().upper(), _mensajes(respuesta)


def autorizar(clave: str, ambiente: str) -> dict:
    """Consulta de autorización. estado: AUTORIZADO | NO AUTORIZADO | EN PROCESO | '' (aún sin respuesta)."""
    cuerpo = ('<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
              'xmlns:ec="http://ec.gob.sri.ws.autorizacion"><soapenv:Header/><soapenv:Body>'
              f'<ec:autorizacionComprobante><claveAccesoComprobante>{clave}</claveAccesoComprobante>'
              '</ec:autorizacionComprobante></soapenv:Body></soapenv:Envelope>')
    respuesta = _post(URLS[ambiente] + 'AutorizacionComprobantesOffline', cuerpo)
    autorizaciones = list(respuesta.iter('autorizacion'))
    if not autorizaciones:
        return {'estado': '', 'mensajes': []}
    # Si hay varias respuestas (reintentos), manda la autorizada
    a = next((x for x in autorizaciones if (x.findtext('estado') or '').upper() == 'AUTORIZADO'), autorizaciones[0])
    return {'estado': (a.findtext('estado') or '').strip().upper(),
            'numero': (a.findtext('numeroAutorizacion') or '').strip(),
            'fecha': (a.findtext('fechaAutorizacion') or '').strip(),
            'comprobante': a.findtext('comprobante') or '',
            'mensajes': _mensajes(a)}
