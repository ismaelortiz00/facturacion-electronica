"""
Pruebas de la factura electrónica: clave de acceso, XML con el código del IVA 8 %, firma
XAdES-BES (verificada además con signxml si está instalado), flujo con el SRI simulado y RIDE.
"""
import base64
from datetime import datetime, timedelta, timezone as tz
from decimal import Decimal as D
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from django.core import mail
from django.test import override_settings
from lxml import etree

from facturacion.models import ErrorNegocio
from facturacion.tests import CARNAVAL, NORMAL, Base

from . import cliente, comprobantes, firma, ride, servicios
from .models import FirmaElectronica


def crear_p12(clave='Clave123', dias=365):
    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COUNTRY_NAME, 'EC'),
                        x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'SECURITY DATA S.A. 2'),
                        x509.NameAttribute(NameOID.COMMON_NAME, 'ANA PEREZ')])
    ahora = datetime.now(tz.utc)
    cert = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre).public_key(llave.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(ahora - timedelta(days=400))
            .not_valid_after(ahora + timedelta(days=dias)).sign(llave, hashes.SHA256()))
    return pkcs12.serialize_key_and_certificates(b'firma', llave, cert, None,
                                                serialization.BestAvailableEncryption(clave.encode()))


def xml(t):
    return etree.fromstring(t.encode())


RECIBIDA = '<r><estado>RECIBIDA</estado></r>'
DEVUELTA = ('<r><estado>DEVUELTA</estado><mensajes><mensaje><identificador>35</identificador>'
            '<mensaje>ARCHIVO NO CUMPLE ESTRUCTURA XML</mensaje><tipo>ERROR</tipo></mensaje></mensajes></r>')
REGISTRADA = ('<r><estado>DEVUELTA</estado><mensajes><mensaje><identificador>43</identificador>'
              '<mensaje>CLAVE ACCESO REGISTRADA</mensaje><tipo>ERROR</tipo></mensaje></mensajes></r>')


def autorizado(clave):
    return (f'<r><autorizaciones><autorizacion><estado>AUTORIZADO</estado><numeroAutorizacion>{clave}</numeroAutorizacion>'
            '<fechaAutorizacion>2026-02-15T10:15:20-05:00</fechaAutorizacion><comprobante><![CDATA[<factura/>]]></comprobante>'
            '</autorizacion></autorizaciones></r>')


class Clave(Base):
    def test_modulo11(self):
        c = comprobantes.clave_acceso(CARNAVAL, '01', '1792146739001', '1', '001', '001', 7, '12345678')
        self.assertEqual(len(c), 49)
        self.assertTrue(comprobantes.revisar_clave(c))
        self.assertFalse(comprobantes.revisar_clave(c[:48] + str((int(c[48]) + 1) % 10)))
        self.assertEqual(comprobantes.codigo_porcentaje('general', D('8')), '8')
        self.assertEqual(comprobantes.codigo_porcentaje('general', D('15')), '4')


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class Electronica(Base):
    def setUp(self):
        super().setUp()
        servicios.configurar_firma(None, crear_p12(), 'Clave123', '1')
        self.f = self.factura(CARNAVAL, [{'servicio_id': self.tour.id, 'cantidad': 2}, {'servicio_id': self.souvenir.id}])

    def test_xml_con_iva_8_y_15_firmado(self):
        ce = servicios.generar_y_firmar(self.f)
        raiz = xml(ce.xml_firmado)
        self.assertEqual(raiz.tag, 'factura')
        cods = {t.findtext('codigoPorcentaje'): t.findtext('valor') for t in raiz.iter('totalImpuesto')}
        self.assertEqual(cods, {'8': '16.00', '4': '3.00'})
        self.assertEqual(raiz.findtext('infoFactura/importeTotal'), '239.00')
        self.assertEqual(raiz.findtext('infoFactura/pagos/pago/formaPago'), '01')
        self.assertIn('Carnaval', etree.tostring(raiz.find('infoAdicional')).decode())
        self.assertTrue(firma.verificar(ce.xml_firmado.encode()))
        self.assertFalse(firma.verificar(ce.xml_firmado.replace('<importeTotal>', '<importeTotal>1').encode()))
        self.assertEqual(servicios.generar_y_firmar(self.f).pk, ce.pk)  # nunca dos claves para un documento

    def test_firma_valida_con_otro_verificador(self):
        try:
            from signxml import DigestAlgorithm, SignatureMethod, XMLVerifier
            from signxml.verifier import SignatureConfiguration
        except ImportError:
            self.skipTest('signxml no instalado (solo se usa en pruebas)')
        ce = servicios.generar_y_firmar(self.f)
        der = base64.b64decode(xml(ce.xml_firmado).find('.//{http://www.w3.org/2000/09/xmldsig#}X509Certificate').text)
        pem = x509.load_der_x509_certificate(der).public_bytes(serialization.Encoding.PEM)
        cfg = SignatureConfiguration(signature_methods=frozenset({SignatureMethod.RSA_SHA1}),
                                     digest_algorithms=frozenset({DigestAlgorithm.SHA1}), expect_references=3)
        XMLVerifier().verify(ce.xml_firmado.encode(), x509_cert=pem, expect_config=cfg)

    def test_autorizado_y_correo(self):
        ce = servicios.generar_y_firmar(self.f)
        with mock.patch.object(cliente, '_post', side_effect=[xml(RECIBIDA), xml(autorizado(ce.clave_acceso))]):
            ce = servicios.procesar(ce)
        self.assertEqual(ce.estado, 'autorizado')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(len(mail.outbox[0].attachments), 2)

    def test_devuelto_y_reintento(self):
        ce = servicios.generar_y_firmar(self.f)
        with mock.patch.object(cliente, '_post', return_value=xml(DEVUELTA)):
            ce = servicios.procesar(ce)
        self.assertEqual(ce.estado, 'devuelto')
        self.assertIn('NO CUMPLE', ce.mensajes)
        with mock.patch.object(cliente, '_post', side_effect=cliente.SinConexion('sin red')):
            ce = servicios.procesar(ce)
        self.assertEqual(ce.estado, 'error')
        with mock.patch.object(cliente, '_post', side_effect=[xml(REGISTRADA), xml(autorizado(ce.clave_acceso))]):
            ce = servicios.procesar(ce)
        self.assertEqual(ce.estado, 'autorizado')

    def test_nota_credito_xml(self):
        from facturacion import servicios as S
        nc = S.emitir(S.guardar(self.u, {'tipo': 'nota_credito', 'fecha': NORMAL, 'cliente_id': self.cliente.id,
                                         'modifica_id': self.f.id, 'motivo': 'Canceló un tour'},
                                [{'servicio_id': self.tour.id}]), self.u)
        raiz = xml(servicios.generar_y_firmar(nc).xml_firmado)
        self.assertEqual(raiz.tag, 'notaCredito')
        self.assertEqual(raiz.findtext('infoNotaCredito/numDocModificado'), self.f.numero)
        self.assertEqual(raiz.findtext('infoNotaCredito/totalConImpuestos/totalImpuesto/codigoPorcentaje'), '8')

    def test_ride_y_clave_cifrada(self):
        self.assertTrue(ride.generar(servicios.generar_y_firmar(self.f)).startswith(b'%PDF'))
        fe = FirmaElectronica.objects.get()
        self.assertNotIn(b'Clave123', bytes(fe.clave_cifrada))
        with self.assertRaises(ErrorNegocio):
            servicios.configurar_firma(None, crear_p12(dias=-1), 'Clave123', '1')
