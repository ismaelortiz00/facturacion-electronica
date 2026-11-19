"""
Pruebas del asistente de IA: herramientas reales, conversación con Claude (simulada: las
pruebas no gastan ni necesitan internet) y modo básico sin clave.
"""
import json
from types import SimpleNamespace
from unittest import mock

from django.test import override_settings

from facturacion.models import Actividad, Comprobante
from facturacion.tests import CARNAVAL, NORMAL, Base

from . import herramientas as H
from . import ia
from .models import ConsultaIA


def bloque_texto(t):
    return SimpleNamespace(type='text', text=t, model_dump=lambda **k: {'type': 'text', 'text': t})


def bloque_herramienta(i, nombre, entrada):
    return SimpleNamespace(type='tool_use', id=i, name=nombre, input=entrada,
                           model_dump=lambda **k: {'type': 'tool_use', 'id': i, 'name': nombre, 'input': entrada})


def respuesta(bloques, fin):
    return SimpleNamespace(content=bloques, stop_reason=fin, usage=SimpleNamespace(input_tokens=100, output_tokens=20))


class Herramientas(Base):
    def test_preparar_factura_queda_en_borrador_con_iva_del_decreto(self):
        r = H.ejecutar('preparar_factura', {'cliente_id': self.cliente.id, 'fecha': CARNAVAL.isoformat(),
                                            'lineas': [{'servicio_id': self.tour.id, 'cantidad': 2}]}, self.u)
        c = Comprobante.objects.get(pk=r['borrador_id'])
        self.assertEqual((c.estado, c.origen, c.total), ('borrador', 'ia', 216))
        self.assertEqual(r['feriado_aplicado'], 'Carnaval')
        self.assertTrue(Actividad.objects.filter(por_ia=True).exists())

    def test_crear_cliente_valida_la_cedula(self):
        self.assertIn('error', H.ejecutar('crear_cliente', {'tipo_identificacion': 'cedula', 'identificacion': '1234567890',
                                                            'nombre': 'X'}, self.u))
        r = H.ejecutar('crear_cliente', {'tipo_identificacion': 'pasaporte', 'identificacion': 'X4587921',
                                         'nombre': 'Emma Schneider'}, self.u)
        self.assertTrue(r['creado'])

    def test_consultas(self):
        self.factura(CARNAVAL, [{'servicio_id': self.tour.id}], forma_pago='credito')
        self.assertEqual(H.ejecutar('estado_iva', {'fecha': CARNAVAL.isoformat()}, self.u)['feriado_vigente']['iva_turistico'], 8)
        v = H.ejecutar('resumen_ventas', {'desde': '2026-02-01', 'hasta': '2026-02-28'}, self.u)
        self.assertEqual((v['base_iva_reducido_feriados'], v['total_vendido']), (100.0, 108.0))
        self.assertEqual(H.ejecutar('cartera', {}, self.u)['total_por_cobrar'], 108.0)
        self.assertEqual(H.ejecutar('consultar_factura', {'numero': '1'}, self.u)['lineas'][0]['iva_pct'], 8.0)
        self.assertIn('error', H.ejecutar('no_existe', {}, self.u))


@override_settings(ANTHROPIC_API_KEY='clave-de-prueba', ANTHROPIC_MODEL='claude-sonnet-5-5')
class ConClaude(Base):
    def test_usa_herramientas_y_responde(self):
        """Claude pide buscar el cliente, luego preparar la factura, y al final responde con texto."""
        pasos = [
            respuesta([bloque_herramienta('t1', 'buscar_clientes', {'texto': 'Luis'})], 'tool_use'),
            respuesta([bloque_herramienta('t2', 'preparar_factura', {'cliente_id': self.cliente.id, 'fecha': CARNAVAL.isoformat(),
                                                                     'lineas': [{'servicio_id': self.tour.id, 'cantidad': 1}]})], 'tool_use'),
            respuesta([bloque_texto('Listo, preparé la factura de Luis Mora por $108.00 con IVA 8 % por Carnaval.')], 'end_turn'),
        ]
        falso = mock.MagicMock()
        falso.messages.create.side_effect = pasos
        with mock.patch('anthropic.Anthropic', return_value=falso):
            r = ia.responder(self.u, [], 'Hazle una factura a Luis por un tour')
        self.assertEqual(r['modo'], 'claude')
        self.assertEqual(r['herramientas'], ['buscar_clientes', 'preparar_factura'])
        self.assertIn('/app/facturas/', r['acciones'][0]['url'])
        # El resultado de la herramienta se le devuelve a Claude
        tercera = falso.messages.create.call_args_list[2].kwargs['messages']
        self.assertEqual(json.loads(tercera[-1]['content'][0]['content'])['total'], 108.0)
        self.assertIn('IVA reducido', falso.messages.create.call_args_list[0].kwargs['system'])

    def test_sin_conexion_cae_a_modo_basico(self):
        falso = mock.MagicMock()
        falso.messages.create.side_effect = ConnectionError('sin red')
        with mock.patch('anthropic.Anthropic', return_value=falso):
            r = ia.responder(self.u, [], '¿Qué IVA rige hoy?')
        self.assertEqual(r['modo'], 'basico')
        self.assertIn('modo básico', r['aviso'])


def gemini(partes, finish='STOP'):
    return {'candidates': [{'content': {'role': 'model', 'parts': partes}, 'finishReason': finish}],
            'usageMetadata': {'promptTokenCount': 900, 'candidatesTokenCount': 40}}


@override_settings(ANTHROPIC_API_KEY='', GEMINI_API_KEY='clave-gemini', GEMINI_MODEL='')
class ConGemini(Base):
    def test_usa_herramientas_y_responde(self):
        pasos = [
            gemini([{'functionCall': {'name': 'buscar_clientes', 'args': {'texto': 'Luis'}}, 'thoughtSignature': 'abc'}]),
            gemini([{'functionCall': {'name': 'preparar_factura', 'args': {
                'cliente_id': self.cliente.id, 'fecha': CARNAVAL.isoformat(), 'lineas': [{'servicio_id': self.tour.id, 'cantidad': 1}]}}}]),
            gemini([{'text': 'pensando…', 'thought': True}, {'text': 'Listo, preparé la factura de Luis Mora por $108.00.'}]),
        ]
        with mock.patch.object(ia, '_llamar_gemini', side_effect=pasos) as llamar:
            r = ia.responder(self.u, [{'rol': 'user', 'texto': 'hola'}, {'rol': 'assistant', 'texto': 'Hola'}],
                             'Hazle una factura a Luis por un tour')
        self.assertEqual(r['modo'], 'gemini')
        self.assertEqual(r['texto'], 'Listo, preparé la factura de Luis Mora por $108.00.')
        self.assertEqual(r['herramientas'], ['buscar_clientes', 'preparar_factura'])
        self.assertIn('/app/facturas/', r['acciones'][0]['url'])
        self.assertEqual(r['tokens'], (2700, 120))
        tercera = llamar.call_args_list[2].args[1]['contents']
        self.assertEqual(tercera[0]['role'], 'user'); self.assertEqual(tercera[1]['role'], 'model')
        self.assertEqual(tercera[3]['parts'][0]['thoughtSignature'], 'abc')  # se devuelve tal cual
        self.assertEqual(tercera[-1]['parts'][0]['functionResponse']['response']['total'], 108.0)
        self.assertEqual(llamar.call_args_list[0].args[0], 'gemini-flash-latest')
        self.assertTrue(Comprobante.objects.filter(origen='ia', estado='borrador').exists())

    def test_esquema_valido_para_gemini(self):
        decl = ia.herramientas_gemini()[0]['functionDeclarations']
        self.assertEqual(len(decl), len(H.HERRAMIENTAS))
        texto = json.dumps(decl)
        self.assertNotIn('"object"', texto); self.assertNotIn('"string"', texto)
        self.assertNotIn('parameters', next(d for d in decl if d['name'] == 'alertas'))

    def test_modelo_inexistente_prueba_el_siguiente(self):
        pasos = [ia.ErrorGemini(404, 'not found'), gemini([{'text': 'Hoy rige el IVA de 15 %.'}])]
        with mock.patch.object(ia, '_llamar_gemini', side_effect=pasos) as llamar:
            r = ia.responder(self.u, [], '¿Qué IVA rige hoy?')
        self.assertEqual(r['modo'], 'gemini')
        self.assertEqual(llamar.call_args_list[1].args[0], ia.GEMINI_MODELOS[1])

    def test_limite_gratuito_cae_a_modo_basico(self):
        with mock.patch.object(ia, '_llamar_gemini', side_effect=ia.ErrorGemini(429, 'quota')):
            r = ia.responder(self.u, [], '¿Qué IVA rige hoy?')
        self.assertEqual(r['modo'], 'basico')
        self.assertIn('límite gratuito', r['aviso'])

    def test_pantalla_muestra_gemini(self):
        self.entrar()
        self.assertIn('Gemini', self.client.get('/asistente/').content.decode())
        self.assertEqual(self.client.get('/asistente/historial/').json()['motor'], 'gemini')


def groq_resp(contenido=None, llamadas=None):
    return {'choices': [{'message': {'role': 'assistant', 'content': contenido, 'tool_calls': llamadas}}],
            'usage': {'prompt_tokens': 800, 'completion_tokens': 30}}


@override_settings(ANTHROPIC_API_KEY='', GEMINI_API_KEY='', GROQ_API_KEY='gsk_prueba', GROQ_MODEL='')
class ConGroq(Base):
    def test_usa_herramientas_y_responde(self):
        pasos = [
            groq_resp(llamadas=[{'id': 'c1', 'type': 'function', 'function': {'name': 'buscar_clientes', 'arguments': '{"texto": "Luis"}'}}]),
            groq_resp(llamadas=[{'id': 'c2', 'type': 'function', 'function': {'name': 'preparar_factura', 'arguments': json.dumps({
                'cliente_id': self.cliente.id, 'fecha': CARNAVAL.isoformat(), 'lineas': [{'servicio_id': self.tour.id, 'cantidad': 1}]})}}]),
            groq_resp('<think>calculo</think>Listo, preparé la factura de Luis Mora por $108.00.'),
        ]
        with mock.patch.object(ia, '_llamar_groq', side_effect=pasos) as llamar:
            r = ia.responder(self.u, [], 'Hazle una factura a Luis por un tour')
        self.assertEqual(r['modo'], 'groq')
        self.assertEqual(r['texto'], 'Listo, preparé la factura de Luis Mora por $108.00.')
        self.assertEqual(r['herramientas'], ['buscar_clientes', 'preparar_factura'])
        self.assertIn('/app/facturas/', r['acciones'][0]['url'])
        ultimo = llamar.call_args_list[2].args[1]['messages']
        self.assertEqual(ultimo[0]['role'], 'system')
        self.assertEqual(ultimo[-1]['role'], 'tool'); self.assertEqual(ultimo[-1]['tool_call_id'], 'c2')
        self.assertEqual(json.loads(ultimo[-1]['content'])['total'], 108.0)

    def test_campos_null_y_reintento(self):
        esquema = next(h for h in ia.H.HERRAMIENTAS if h['name'] == 'resumen_ventas')['input_schema']
        self.assertEqual(ia._admite_vacio(esquema)['properties']['desde']['type'], ['string', 'null'])
        pasos = [ia.ErrorGroq(400, "tool call validation failed: parameters for tool resumen_ventas did not match schema"),
                 groq_resp(llamadas=[{'id': 'c1', 'type': 'function', 'function': {
                     'name': 'resumen_ventas', 'arguments': '{"desde": null, "hasta": null, "inventado": 1}'}}]),
                 groq_resp('Vendiste lo indicado.')]
        with mock.patch.object(ia, '_llamar_groq', side_effect=pasos) as llamar:
            r = ia.responder(self.u, [], '¿Cuánto vendí este mes?')
        self.assertEqual(r['modo'], 'groq')
        resultado = json.loads(llamar.call_args_list[2].args[1]['messages'][-1]['content'])
        self.assertNotIn('error', resultado)

    def test_argumentos_rotos_no_rompen(self):
        pasos = [groq_resp(llamadas=[{'id': 'c1', 'type': 'function', 'function': {'name': 'buscar_clientes', 'arguments': '{mal'}}]),
                 groq_resp('¿A qué cliente buscas?')]
        with mock.patch.object(ia, '_llamar_groq', side_effect=pasos):
            r = ia.responder(self.u, [], 'busca')
        self.assertEqual(r['modo'], 'groq')

    @override_settings(GEMINI_API_KEY='AQ.x')
    def test_si_groq_falla_prueba_gemini(self):
        with mock.patch.object(ia, '_llamar_groq', side_effect=ia.ErrorGroq(429, 'rate')), \
                mock.patch.object(ia, '_llamar_gemini', return_value=gemini([{'text': 'IVA 15 %.'}])):
            r = ia.responder(self.u, [], '¿Qué IVA rige hoy?')
        self.assertEqual(r['modo'], 'gemini')
        self.assertIn('límite gratuito de Groq', r['aviso'])

    @override_settings(GEMINI_API_KEY='AQ.x')
    def test_gemini_sin_plan_gratis_explica(self):
        with mock.patch.object(ia, '_llamar_groq', side_effect=ia.ErrorGroq(401, 'Invalid API Key')), \
                mock.patch.object(ia, '_llamar_gemini', side_effect=ia.ErrorGemini(403, 'The Gemini API free tier is not available for this project.')):
            r = ia.responder(self.u, [], '¿Qué IVA rige hoy?')
        self.assertEqual(r['modo'], 'basico')
        self.assertIn('GROQ_API_KEY no es válida', r['aviso'])
        self.assertIn('plan gratuito de Gemini', r['aviso'])


class ModoBasico(Base):
    def test_preguntas_frecuentes(self):
        self.factura(CARNAVAL, [{'servicio_id': self.tour.id}], forma_pago='credito')
        self.assertIn('8 %', ia.modo_basico(self.u, '¿Qué IVA hay el 15 de febrero?')['texto'])
        self.assertIn('Luis Mora', ia.modo_basico(self.u, '¿Quién me debe?')['texto'])
        self.assertIn('IVA cobrado', ia.modo_basico(self.u, '¿Cuánto IVA declaro?')['texto'])

    def test_sin_datos_pregunta_y_no_crea_nada(self):
        for texto in ['Prepara una factura para mí', 'hazme una factura', 'factura para Luis Mora']:
            r = ia.modo_basico(self.u, texto)
            self.assertIn('?', r['texto'], texto)
        self.assertFalse(Comprobante.objects.exists())

    def test_factura_por_texto(self):
        r = ia.modo_basico(self.u, 'Factura para Luis Mora: 2 tour mindo y 1 camiseta')
        c = Comprobante.objects.get()
        self.assertEqual((c.estado, c.lineas.count()), ('borrador', 2))
        self.assertEqual(r['acciones'][0]['url'], f'/app/facturas/{c.id}/')

    def test_chat_por_la_web_guarda_historial_y_registro(self):
        self.entrar()
        r = self.client.post('/asistente/mensaje/', json.dumps({'texto': '¿Quién me debe?'}), content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.client.get('/asistente/historial/').json()['mensajes']), 2)
        self.assertEqual(ConsultaIA.objects.count(), 1)
