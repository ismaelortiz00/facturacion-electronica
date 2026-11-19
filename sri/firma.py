"""
Firma XAdES-BES que exige el SRI (RSA-SHA1, C14N, firma envuelta en el comprobante).
Se firma con el archivo .p12 que entrega la entidad certificadora (Security Data,
Banco Central, ANF, Uanataca, etc.). No se usa ningún servicio externo.
"""
import base64
import hashlib
import random
from datetime import datetime

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import pkcs12
from django.utils import timezone
from lxml import etree

DS = 'http://www.w3.org/2000/09/xmldsig#'
ETSI = 'http://uri.etsi.org/01903/v1.3.2#'
C14N = 'http://www.w3.org/TR/2001/REC-xml-c14n-20010315'
NS = {'ds': DS, 'etsi': ETSI}


class ErrorFirma(Exception):
    pass


def cargar_p12(contenido: bytes, clave: str):
    """Devuelve (llave privada, certificado de firma). Valida clave y vigencia."""
    try:
        llave, cert, _ = pkcs12.load_key_and_certificates(contenido, clave.encode())
    except ValueError:
        raise ErrorFirma('La clave de la firma no es correcta o el archivo no es un .p12 válido.')
    if llave is None or cert is None:
        raise ErrorFirma('El archivo .p12 no contiene la llave privada y el certificado.')
    ahora = timezone.now()
    vence = cert.not_valid_after_utc
    if vence < ahora:
        raise ErrorFirma(f'La firma electrónica venció el {vence:%d/%m/%Y}. Renuévala con tu entidad certificadora.')
    return llave, cert


def datos_certificado(cert) -> dict:
    def nombre(n):
        cn = [a.value for a in n if a.oid._name == 'commonName']
        return cn[0] if cn else n.rfc4514_string()
    return {'titular': nombre(cert.subject), 'emisor': nombre(cert.issuer), 'vence': cert.not_valid_after_utc}


def _sha1_b64(datos: bytes) -> str:
    return base64.b64encode(hashlib.sha1(datos).digest()).decode()


def _c14n(nodo) -> bytes:
    return etree.tostring(nodo, method='c14n', exclusive=False, with_comments=False)


def _b64_lineas(datos: bytes) -> str:
    texto = base64.b64encode(datos).decode()
    return '\n'.join(texto[i:i + 76] for i in range(0, len(texto), 76))


def firmar(raiz: etree._Element, llave, cert) -> bytes:
    """Firma el comprobante (elemento raíz con id="comprobante") y devuelve el XML en UTF-8."""
    n = lambda: str(random.SystemRandom().randint(100000, 999999))
    ids = {k: n() for k in ('firma', 'info', 'prop', 'cert', 'ref', 'obj', 'valor', 'propref')}
    firma_id = f'Signature{ids["firma"]}'

    # 1. Digest del comprobante (transformación "enveloped": sin la firma)
    digest_doc = _sha1_b64(_c14n(raiz))

    def ds(padre, etiqueta, texto=None, **attr):
        nodo = etree.SubElement(padre, f'{{{DS}}}{etiqueta}', **attr)
        if texto is not None:
            nodo.text = texto
        return nodo

    def et(padre, etiqueta, texto=None, **attr):
        nodo = etree.SubElement(padre, f'{{{ETSI}}}{etiqueta}', **attr)
        if texto is not None:
            nodo.text = texto
        return nodo

    firma = etree.SubElement(raiz, f'{{{DS}}}Signature', nsmap=NS, Id=firma_id)
    signed_info = ds(firma, 'SignedInfo', Id=f'Signature-SignedInfo{ids["info"]}')
    signature_value = ds(firma, 'SignatureValue', Id=f'SignatureValue{ids["valor"]}')

    # 2. KeyInfo: certificado y llave pública
    key_info = ds(firma, 'KeyInfo', Id=f'Certificate{ids["cert"]}')
    x509 = ds(key_info, 'X509Data')
    der = cert.public_bytes(serialization.Encoding.DER)
    ds(x509, 'X509Certificate', _b64_lineas(der))
    numeros = cert.public_key().public_numbers()
    rsa = ds(ds(key_info, 'KeyValue'), 'RSAKeyValue')
    ds(rsa, 'Modulus', _b64_lineas(numeros.n.to_bytes((numeros.n.bit_length() + 7) // 8, 'big')))
    ds(rsa, 'Exponent', base64.b64encode(numeros.e.to_bytes((numeros.e.bit_length() + 7) // 8, 'big')).decode())

    # 3. Propiedades firmadas (XAdES)
    objeto = ds(firma, 'Object', Id=f'{firma_id}-Object{ids["obj"]}')
    qp = et(objeto, 'QualifyingProperties', Target=f'#{firma_id}')
    sp = et(qp, 'SignedProperties', Id=f'{firma_id}-SignedProperties{ids["prop"]}')
    ssp = et(sp, 'SignedSignatureProperties')
    et(ssp, 'SigningTime', timezone.localtime().isoformat(timespec='seconds'))
    cert_nodo = et(et(ssp, 'SigningCertificate'), 'Cert')
    cd = et(cert_nodo, 'CertDigest')
    ds(cd, 'DigestMethod', Algorithm=f'{DS}sha1')
    ds(cd, 'DigestValue', _sha1_b64(der))
    isr = et(cert_nodo, 'IssuerSerial')
    ds(isr, 'X509IssuerName', cert.issuer.rfc4514_string())
    ds(isr, 'X509SerialNumber', str(cert.serial_number))
    dof = et(et(sp, 'SignedDataObjectProperties'), 'DataObjectFormat', ObjectReference=f'#Reference-ID-{ids["ref"]}')
    et(dof, 'Description', 'contenido comprobante')
    et(dof, 'MimeType', 'text/xml')

    # 4. SignedInfo con las tres referencias (se calculan ya dentro del árbol: C14N incluye los namespaces)
    ds(signed_info, 'CanonicalizationMethod', Algorithm=C14N)
    ds(signed_info, 'SignatureMethod', Algorithm=f'{DS}rsa-sha1')
    r1 = ds(signed_info, 'Reference', Id=f'SignedPropertiesID{ids["propref"]}',
            Type='http://uri.etsi.org/01903#SignedProperties', URI=f'#{sp.get("Id")}')
    ds(r1, 'DigestMethod', Algorithm=f'{DS}sha1')
    ds(r1, 'DigestValue', _sha1_b64(_c14n(sp)))
    r2 = ds(signed_info, 'Reference', URI=f'#{key_info.get("Id")}')
    ds(r2, 'DigestMethod', Algorithm=f'{DS}sha1')
    ds(r2, 'DigestValue', _sha1_b64(_c14n(key_info)))
    r3 = ds(signed_info, 'Reference', Id=f'Reference-ID-{ids["ref"]}', URI='#comprobante')
    ds(ds(r3, 'Transforms'), 'Transform', Algorithm=f'{DS}enveloped-signature')
    ds(r3, 'DigestMethod', Algorithm=f'{DS}sha1')
    ds(r3, 'DigestValue', digest_doc)

    # 5. Valor de la firma sobre SignedInfo canonicalizado
    valor = llave.sign(_c14n(signed_info), padding.PKCS1v15(), hashes.SHA1())
    signature_value.text = _b64_lineas(valor)
    return etree.tostring(raiz, xml_declaration=True, encoding='UTF-8')


def verificar(xml: bytes) -> bool:
    """Comprueba digests y firma (sirve para pruebas y para revisar un XML antes de enviarlo)."""
    raiz = etree.fromstring(xml)
    firma = raiz.find(f'{{{DS}}}Signature')
    if firma is None:
        return False
    signed_info = firma.find(f'{{{DS}}}SignedInfo')
    for ref in signed_info.findall(f'{{{DS}}}Reference'):
        uri = ref.get('URI')[1:]
        esperado = ref.find(f'{{{DS}}}DigestValue').text
        if uri == 'comprobante':
            copia = etree.fromstring(etree.tostring(raiz))
            copia.remove(copia.find(f'{{{DS}}}Signature'))
            calculado = _sha1_b64(_c14n(copia))
        else:
            nodo = raiz.xpath(f'//*[@Id="{uri}"]')
            if not nodo:
                return False
            calculado = _sha1_b64(_c14n(nodo[0]))
        if calculado != esperado:
            return False
    der = base64.b64decode(firma.find(f'.//{{{DS}}}X509Certificate').text)
    from cryptography import x509 as x
    clave_publica = x.load_der_x509_certificate(der).public_key()
    valor = base64.b64decode(firma.find(f'{{{DS}}}SignatureValue').text)
    try:
        clave_publica.verify(valor, _c14n(signed_info), padding.PKCS1v15(), hashes.SHA1())
        return True
    except Exception:
        return False
