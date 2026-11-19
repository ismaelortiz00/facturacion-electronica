"""
Pruebas automáticas: python manage.py test cuentas
"""
import time
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings

from .models import RegistroAcceso, Usuario
from .validadores import revisar_cedula

CLAVE = 'Contable#2026'


TOKEN2 = 'a' * 64


def resolver_captcha(cliente, hace=5, respuesta='respuesta-valida'):
    """Simula a una persona: el servidor emitió el token 2 hace unos segundos
    y el widget de hCaptcha devolvió una respuesta válida (token 1)."""
    sesion = cliente.session
    sesion['token2'] = {'valor': TOKEN2, 'emitido': time.time() - hace}
    sesion.save()
    return {'token2': TOKEN2, 'h-captcha-response': respuesta}


# La verificación con hCaptcha se simula: solo 'respuesta-valida' es aceptada
@patch('cuentas.captcha.verificar_con_proveedor', lambda respuesta, ip: respuesta == 'respuesta-valida')
@override_settings(VERIFICAR_DOMINIO_CORREO=False)
class PruebasLogin(TestCase):

    def setUp(self):
        self.usuario = Usuario.objects.create_user('0903686962', 'ana@empresa.ec', 'Ana Pérez', CLAVE)
        self.usuario.correo_verificado = True
        self.usuario.save()

    def login(self, usuario='0903686962', clave=CLAVE):
        return self.client.post('/login/validar/', {'usuario': usuario, 'clave': clave,
                                                    **resolver_captcha(self.client)}).json()

    # --- Cédula --------------------------------------------------------
    def test_cedulas(self):
        self.assertTrue(revisar_cedula('0903686962')[0])
        self.assertTrue(revisar_cedula('1710034065')[0])
        self.assertFalse(revisar_cedula('1714307104')[0])   # verificador incorrecto
        self.assertFalse(revisar_cedula('2512345678')[0])   # provincia 25 no existe
        self.assertFalse(revisar_cedula('0962345678')[0])   # tercer dígito 6
        self.assertFalse(revisar_cedula('12345')[0])

    # --- Contraseña encriptada ----------------------------------------
    def test_clave_encriptada_con_argon2(self):
        self.assertTrue(self.usuario.password.startswith('argon2'))
        self.assertNotIn(CLAVE, self.usuario.password)

    # --- Captcha -------------------------------------------------------
    def test_login_sin_captcha_es_rechazado(self):
        r = self.client.post('/login/validar/', {'usuario': '0903686962', 'clave': CLAVE}).json()
        self.assertFalse(r['success'])
        self.assertTrue(r['token2'])  # se entrega un token nuevo para reintentar

    def test_token1_invalido_es_rechazado(self):
        datos = resolver_captcha(self.client, respuesta='falsa')
        r = self.client.post('/login/validar/', {'usuario': '0903686962', 'clave': CLAVE, **datos}).json()
        self.assertFalse(r['success'])
        self.assertIn('No soy un robot', r['message'])

    def test_envio_demasiado_rapido(self):
        datos = resolver_captcha(self.client, hace=0)
        r = self.client.post('/login/validar/', {'usuario': '0903686962', 'clave': CLAVE, **datos}).json()
        self.assertIn('Demasiado rápido', r['message'])

    def test_campo_trampa_bloquea_bots(self):
        datos = resolver_captcha(self.client)
        r = self.client.post('/login/validar/', {'usuario': '0903686962', 'clave': CLAVE,
                                                 'website': 'spam', **datos}).json()
        self.assertFalse(r['success'])

    def test_token2_es_de_un_solo_uso(self):
        datos = {'usuario': '0903686962', 'clave': CLAVE, **resolver_captcha(self.client)}
        self.assertTrue(self.client.post('/login/validar/', datos).json()['success'])
        self.client.logout()
        self.assertFalse(self.client.post('/login/validar/', datos).json()['success'])

    def test_pagina_de_login_incluye_hcaptcha(self):
        html = self.client.get('/').content.decode()
        self.assertIn('h-captcha', html)
        self.assertIn('js.hcaptcha.com', html)

    # --- Login ---------------------------------------------------------
    def test_login_con_cedula_y_con_correo(self):
        self.assertTrue(self.login()['success'])
        self.client.logout()
        self.assertTrue(self.login(usuario='ana@empresa.ec')['success'])

    def test_bloqueo_tras_tres_intentos(self):
        self.assertEqual(self.login(clave='Mala#1234')['intentos_restantes'], 2)
        self.assertEqual(self.login(clave='Mala#1234')['intentos_restantes'], 1)
        with self.captureOnCommitCallbacks(execute=True):  # el aviso se envía al confirmar la transacción
            r = self.login(clave='Mala#1234')
        self.assertEqual(r['intentos_restantes'], 0)
        self.assertGreater(r['segundos_bloqueo'], 0)
        # Ni con la clave correcta entra mientras dura el bloqueo
        r = self.login()
        self.assertFalse(r['success'])
        self.assertIn('bloqueada', r['message'])
        self.assertEqual(len(mail.outbox), 1)  # aviso de bloqueo por correo

    def test_correo_no_verificado_no_entra(self):
        Usuario.objects.create_user('1710034065', 'luis@empresa.ec', 'Luis Mora', CLAVE)
        r = self.login(usuario='1710034065')
        self.assertFalse(r['success'])
        self.assertTrue(r.get('reenviar'))

    # --- Registro y verificación --------------------------------------
    def registrar(self):
        datos = {'cedula': '1710034065', 'nombres': 'Luis Mora', 'correo': 'luis@empresa.ec',
                 'clave': CLAVE, 'confirmar': CLAVE, **resolver_captcha(self.client)}
        return self.client.post('/registro/procesar/', datos).json()

    def codigo_del_correo(self):
        return mail.outbox[-1].body.split(': ')[1].strip()

    def test_registro_y_verificacion_con_codigo(self):
        r = self.registrar()
        self.assertTrue(r['success'], r)
        self.assertTrue(r['verificar'])
        nuevo = Usuario.objects.get(cedula='1710034065')
        self.assertFalse(nuevo.correo_verificado)

        codigo = self.codigo_del_correo()
        self.assertEqual(len(codigo), 6)
        self.assertNotEqual(nuevo.token_verificacion, codigo)  # en la BD solo está el hash

        r = self.client.post('/verificar-correo/codigo/', {'correo': 'luis@empresa.ec', 'codigo': codigo}).json()
        self.assertTrue(r['success'], r)
        nuevo.refresh_from_db()
        self.assertTrue(nuevo.correo_verificado)
        # el código no sirve dos veces
        r = self.client.post('/verificar-correo/codigo/', {'correo': 'luis@empresa.ec', 'codigo': codigo}).json()
        self.assertFalse(r['success'])

    def test_codigo_incorrecto_limita_intentos(self):
        self.registrar()
        codigo = self.codigo_del_correo()
        malo = '000000' if codigo != '000000' else '111111'
        for restantes in (4, 3, 2, 1, 0):
            r = self.client.post('/verificar-correo/codigo/', {'correo': 'luis@empresa.ec', 'codigo': malo}).json()
            self.assertIn(f'{restantes} intento', r['message'])
        # ni con el código correcto, hay que pedir uno nuevo
        r = self.client.post('/verificar-correo/codigo/', {'correo': 'luis@empresa.ec', 'codigo': codigo}).json()
        self.assertIn('Reenviar código', r['message'])

    def test_reenviar_codigo_invalida_el_anterior(self):
        self.registrar()
        viejo = self.codigo_del_correo()
        self.client.post('/verificar-correo/reenviar/', {'correo': 'luis@empresa.ec'})
        nuevo = self.codigo_del_correo()
        if viejo != nuevo:
            r = self.client.post('/verificar-correo/codigo/', {'correo': 'luis@empresa.ec', 'codigo': viejo}).json()
            self.assertFalse(r['success'])
        r = self.client.post('/verificar-correo/codigo/', {'correo': 'luis@empresa.ec', 'codigo': nuevo}).json()
        self.assertTrue(r['success'])

    def test_registro_rechaza_cedula_invalida_y_clave_debil(self):
        base = {'nombres': 'Luis Mora', 'correo': 'luis@empresa.ec'}
        r = self.client.post('/registro/procesar/', {**base, 'cedula': '1714307104', 'clave': CLAVE,
                                                     'confirmar': CLAVE, **resolver_captcha(self.client)}).json()
        self.assertIn('cédula', r['message'])
        r = self.client.post('/registro/procesar/', {**base, 'cedula': '1710034065', 'clave': 'abc12345',
                                                     'confirmar': 'abc12345', **resolver_captcha(self.client)}).json()
        self.assertIn('mayúscula', r['message'])

    # --- Auditoría y panel --------------------------------------------
    def test_panel_protegido_y_bitacora(self):
        self.assertEqual(self.client.get('/panel/').status_code, 302)
        self.login()
        r = self.client.get('/panel/', follow=True)  # el panel lleva al sistema contable
        self.assertEqual(r.status_code, 200)
        self.assertIn('/app/', r.redirect_chain[0][0])
        self.assertTrue(RegistroAcceso.objects.filter(accion='LOGIN', estado='OK').exists())
