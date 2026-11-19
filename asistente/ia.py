"""
ASISTENTE DE IA
Con clave de Anthropic usa Claude, y con clave de Google usa Gemini (tiene uso gratuito). En los dos casos
la IA trabaja con "herramientas": decide qué consultar o preparar, el sistema lo ejecuta con las reglas
del negocio y la IA redacta la respuesta.
Sin clave o sin internet funciona en MODO BÁSICO: entiende las preguntas más comunes por
palabras clave y usa las mismas herramientas, para que el sistema nunca se quede sin asistente.
"""
import json
import re
import time
import urllib.error
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from facturacion import servicios as S
from facturacion.models import Empleado, Empresa

from . import herramientas as H

MAX_PASOS = 8
DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']


def prompt_sistema(usuario, pagina=''):
    hoy = timezone.localdate()
    empresa = Empresa.actual()
    emp = Empleado.objects.filter(usuario=usuario).first()
    try:
        e = S.estado_iva(hoy)
        iva = (f'Hoy rige feriado de {e["decreto"].feriado}: servicios turísticos al {e["decreto"].porcentaje.normalize():f} %, '
               f'el resto al {e["general"].normalize():f} %.' if e['decreto'] else
               f'Hoy no hay feriado con decreto: IVA general {e["general"].normalize():f} %.')
    except Exception:
        iva = ''
    contexto = ''
    m = re.search(r'/app/facturas/(\d+)/', pagina or '')
    if m:
        contexto = f'\nEl usuario está viendo la factura con id interno {m.group(1)}.'
    return f"""Eres el asistente de facturación de {empresa or 'un negocio turístico'} en Ecuador, dentro del sistema
"Facturación Turística IK". Hablas con {usuario.nombres} ({emp.get_rol_display() if emp else 'usuario'}).
Hoy es {DIAS[hoy.weekday()]} {hoy:%d/%m/%Y} ({hoy.isoformat()}). {iva}{contexto}

Lo que distingue a este sistema: en los feriados con decreto ejecutivo, los servicios turísticos pagan un IVA reducido
(normalmente 8 %) en vez de la tarifa general (15 %). El sistema lo aplica solo según la fecha de la factura.

Reglas:
- Para cualquier dato del negocio (ventas, clientes, facturas, deudas, IVA, feriados) usa SIEMPRE las herramientas.
  Nunca inventes cifras, nombres ni números de factura.
- Para facturar: busca el cliente (buscar_clientes) y los servicios (buscar_servicios); si el cliente no existe, pide
  sus datos y regístralo con crear_cliente; luego usa preparar_factura. La factura queda en BORRADOR: dile al usuario
  que la revise y presione «Emitir». Tú no emites ni anulas nada.
- Si hay varias coincidencias de cliente o servicio, pregunta cuál antes de preparar la factura.
- Si el usuario no dijo el cliente o los servicios, PREGUNTA. Nunca uses datos de ejemplo ni supongas un cliente.
- Explica el IVA con palabras sencillas cuando lo pregunten (por qué 8 % o 15 %).
- Responde en español, corto y claro, como un compañero de trabajo. Valores en dólares con 2 decimales ($1,234.50).
- Si algo no se puede hacer en el sistema, dilo y sugiere el paso en la pantalla.
"""


# ---------------------------------------------------------------------
#  Claude
# ---------------------------------------------------------------------
def con_claude(usuario, historial: list, texto: str, pagina='') -> dict:
    import anthropic
    cliente = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=60, max_retries=1)
    mensajes = [{'role': m['rol'], 'content': m['texto']} for m in historial[-12:]] + [{'role': 'user', 'content': texto}]
    usadas, acciones, entrada, salida = [], [], 0, 0
    for _ in range(MAX_PASOS):
        r = cliente.messages.create(model=settings.ANTHROPIC_MODEL, max_tokens=1500,
                                    system=prompt_sistema(usuario, pagina), tools=H.HERRAMIENTAS, messages=mensajes)
        entrada += getattr(r.usage, 'input_tokens', 0) or 0
        salida += getattr(r.usage, 'output_tokens', 0) or 0
        if r.stop_reason != 'tool_use':
            respuesta = '\n'.join(b.text for b in r.content if getattr(b, 'type', '') == 'text').strip()
            return {'texto': respuesta or 'Listo.', 'acciones': acciones, 'herramientas': usadas, 'modo': 'claude',
                    'tokens': (entrada, salida)}
        mensajes.append({'role': 'assistant', 'content': [b.model_dump(exclude_none=True) for b in r.content]})
        resultados = []
        for b in r.content:
            if getattr(b, 'type', '') != 'tool_use':
                continue
            resultado = H.ejecutar(b.name, b.input, usuario)
            usadas.append(b.name)
            acciones += _acciones(b.name, resultado)
            resultados.append({'type': 'tool_result', 'tool_use_id': b.id,
                               'content': json.dumps(resultado, ensure_ascii=False, default=str)})
        mensajes.append({'role': 'user', 'content': resultados})
    return {'texto': 'La consulta necesitó demasiados pasos; intenta pedirlo en partes.', 'acciones': acciones,
            'herramientas': usadas, 'modo': 'claude', 'tokens': (entrada, salida)}


# ---------------------------------------------------------------------
#  Gemini (Google) — misma lógica que Claude, por la API REST (no necesita librerías extra)
# ---------------------------------------------------------------------
GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent'
# Si no se elige modelo en el .env, se prueban en orden (el primero es el alias que Google mantiene al día)
GEMINI_MODELOS = ['gemini-flash-latest', 'gemini-2.5-flash', 'gemini-flash-lite-latest']


class ErrorGemini(Exception):
    def __init__(self, codigo, mensaje):
        super().__init__(f'{codigo}: {mensaje}')
        self.codigo = codigo
        self.mensaje = mensaje


def _esquema_gemini(esquema: dict) -> dict:
    """Convierte el JSON Schema de las herramientas al formato que acepta Gemini."""
    salida = {}
    for clave, valor in esquema.items():
        if clave == 'type':
            salida['type'] = str(valor).upper()
        elif clave == 'properties':
            salida['properties'] = {k: _esquema_gemini(v) for k, v in valor.items()}
        elif clave == 'items':
            salida['items'] = _esquema_gemini(valor)
        elif clave in ('description', 'enum', 'required', 'format', 'nullable'):
            salida[clave] = valor
    return salida


def herramientas_gemini() -> list:
    declaraciones = []
    for h in H.HERRAMIENTAS:
        d = {'name': h['name'], 'description': h['description']}
        if h.get('input_schema', {}).get('properties'):  # Gemini no acepta objetos sin propiedades
            d['parameters'] = _esquema_gemini(h['input_schema'])
        declaraciones.append(d)
    return [{'functionDeclarations': declaraciones}]


def _llamar_gemini(modelo, cuerpo):
    pedido = urllib.request.Request(GEMINI_URL.format(modelo=modelo), data=json.dumps(cuerpo).encode(), method='POST',
                                    headers={'Content-Type': 'application/json', 'x-goog-api-key': settings.GEMINI_API_KEY})
    try:
        with urllib.request.urlopen(pedido, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as error:
        try:
            mensaje = json.loads(error.read().decode()).get('error', {}).get('message', '')
        except Exception:
            mensaje = ''
        raise ErrorGemini(error.code, mensaje or error.reason)


def con_gemini(usuario, historial: list, texto: str, pagina='') -> dict:
    contenidos = [{'role': 'model' if m['rol'] == 'assistant' else 'user', 'parts': [{'text': m['texto']}]}
                  for m in historial[-12:] if m.get('texto')]
    contenidos.append({'role': 'user', 'parts': [{'text': texto}]})
    base = {'systemInstruction': {'parts': [{'text': prompt_sistema(usuario, pagina)}]},
            'tools': herramientas_gemini(), 'generationConfig': {'maxOutputTokens': 2048}}
    modelos = [settings.GEMINI_MODEL] if settings.GEMINI_MODEL else list(GEMINI_MODELOS)
    usadas, acciones, entrada, salida = [], [], 0, 0
    for _ in range(MAX_PASOS):
        while True:
            try:
                r = _llamar_gemini(modelos[0], {**base, 'contents': contenidos})
                break
            except ErrorGemini as error:
                if error.codigo == 404 and len(modelos) > 1:  # ese modelo ya no existe: prueba el siguiente
                    modelos.pop(0)
                    continue
                raise
        uso = r.get('usageMetadata', {})
        entrada += uso.get('promptTokenCount', 0) or 0
        salida += uso.get('candidatesTokenCount', 0) or 0
        candidato = (r.get('candidates') or [{}])[0]
        contenido = candidato.get('content') or {}
        partes = contenido.get('parts') or []
        llamadas = [p['functionCall'] for p in partes if p.get('functionCall')]
        if not llamadas:
            respuesta = '\n'.join(p['text'] for p in partes if p.get('text') and not p.get('thought')).strip()
            if not respuesta and candidato.get('finishReason') == 'SAFETY':
                respuesta = 'No puedo responder eso. Pregúntame sobre facturas, clientes, IVA o cobros.'
            return {'texto': respuesta or 'Listo.', 'acciones': acciones, 'herramientas': usadas, 'modo': 'gemini',
                    'tokens': (entrada, salida)}
        contenidos.append({'role': 'model', 'parts': partes})  # se devuelve tal cual (incluye firmas internas)
        respuestas = []
        for llamada in llamadas:
            resultado = H.ejecutar(llamada.get('name', ''), llamada.get('args') or {}, usuario)
            usadas.append(llamada.get('name', ''))
            acciones += _acciones(llamada.get('name', ''), resultado)
            datos = json.loads(json.dumps(resultado, ensure_ascii=False, default=str))
            respuesta = {'name': llamada.get('name', ''), 'response': datos if isinstance(datos, dict) else {'resultado': datos}}
            if llamada.get('id'):
                respuesta['id'] = llamada['id']
            respuestas.append({'functionResponse': respuesta})
        contenidos.append({'role': 'user', 'parts': respuestas})
    return {'texto': 'La consulta necesitó demasiados pasos; intenta pedirlo en partes.', 'acciones': acciones,
            'herramientas': usadas, 'modo': 'gemini', 'tokens': (entrada, salida)}


# ---------------------------------------------------------------------
#  Groq (gratis, sin tarjeta) — API compatible con OpenAI
# ---------------------------------------------------------------------
GROQ_URL = 'https://api.groq.com/openai/v1/chat/completions'
GROQ_MODELOS = ['openai/gpt-oss-120b', 'llama-3.3-70b-versatile', 'qwen/qwen3-32b']


class ErrorGroq(ErrorGemini):
    pass


def _llamar_groq(modelo, cuerpo):
    pedido = urllib.request.Request(GROQ_URL, data=json.dumps({**cuerpo, 'model': modelo}).encode(), method='POST',
                                    headers={'Content-Type': 'application/json', 'User-Agent': 'FacturacionTuristicaIK/1.0',
                                             'Authorization': f'Bearer {settings.GROQ_API_KEY}'})
    try:
        with urllib.request.urlopen(pedido, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as error:
        try:
            mensaje = json.loads(error.read().decode()).get('error', {}).get('message', '')
        except Exception:
            mensaje = ''
        raise ErrorGroq(error.code, mensaje or str(error.reason))


def _admite_vacio(esquema: dict) -> dict:
    """Groq valida estrictamente: los campos opcionales deben aceptar null (los modelos a veces lo mandan)."""
    esquema = dict(esquema)
    if 'properties' in esquema:
        requeridos = set(esquema.get('required', []))
        props = {}
        for nombre, sub in esquema['properties'].items():
            sub = _admite_vacio(sub)
            if nombre not in requeridos and isinstance(sub.get('type'), str):
                sub['type'] = [sub['type'], 'null']
                if 'enum' in sub:
                    sub['enum'] = list(sub['enum']) + [None]
            props[nombre] = sub
        esquema['properties'] = props
    if isinstance(esquema.get('items'), dict):
        esquema['items'] = _admite_vacio(esquema['items'])
    return esquema


def con_groq(usuario, historial: list, texto: str, pagina='') -> dict:
    mensajes = [{'role': 'system', 'content': prompt_sistema(usuario, pagina)}]
    mensajes += [{'role': 'assistant' if m['rol'] == 'assistant' else 'user', 'content': m['texto']}
                 for m in historial[-12:] if m.get('texto')]
    mensajes.append({'role': 'user', 'content': texto})
    herramientas = [{'type': 'function', 'function': {'name': h['name'], 'description': h['description'],
                                                      'parameters': _admite_vacio(h['input_schema'])}} for h in H.HERRAMIENTAS]
    modelos = [settings.GROQ_MODEL] if settings.GROQ_MODEL else list(GROQ_MODELOS)
    usadas, acciones, entrada, salida = [], [], 0, 0
    for _ in range(MAX_PASOS):
        reintentos = 2
        while True:
            try:
                r = _llamar_groq(modelos[0], {'messages': mensajes, 'tools': herramientas, 'tool_choice': 'auto',
                                              'max_tokens': 1500, 'temperature': 0.2})
                break
            except ErrorGroq as error:
                # El modelo armó mal una llamada a herramienta: se le vuelve a pedir (suele salir bien)
                if error.codigo == 400 and ('tool call' in error.mensaje.lower() or 'failed to call' in error.mensaje.lower()) \
                        and reintentos > 0:
                    reintentos -= 1
                    continue
                no_existe = error.codigo == 404 or 'decommission' in error.mensaje or 'does not exist' in error.mensaje
                if no_existe and len(modelos) > 1:
                    modelos.pop(0)
                    continue
                raise
        uso = r.get('usage') or {}
        entrada += uso.get('prompt_tokens', 0) or 0
        salida += uso.get('completion_tokens', 0) or 0
        mensaje = (r.get('choices') or [{}])[0].get('message') or {}
        llamadas = mensaje.get('tool_calls') or []
        if not llamadas:
            respuesta = re.sub(r'<think>.*?</think>', '', mensaje.get('content') or '', flags=re.S).strip()
            return {'texto': respuesta or 'Listo.', 'acciones': acciones, 'herramientas': usadas, 'modo': 'groq',
                    'tokens': (entrada, salida)}
        mensajes.append({'role': 'assistant', 'content': mensaje.get('content') or '', 'tool_calls': llamadas})
        for llamada in llamadas:
            nombre = llamada.get('function', {}).get('name', '')
            try:
                argumentos = json.loads(llamada.get('function', {}).get('arguments') or '{}')
            except json.JSONDecodeError:
                argumentos = {}
            resultado = H.ejecutar(nombre, argumentos if isinstance(argumentos, dict) else {}, usuario)
            usadas.append(nombre)
            acciones += _acciones(nombre, resultado)
            mensajes.append({'role': 'tool', 'tool_call_id': llamada.get('id', ''),
                             'content': json.dumps(resultado, ensure_ascii=False, default=str)})
    return {'texto': 'La consulta necesitó demasiados pasos; intenta pedirlo en partes.', 'acciones': acciones,
            'herramientas': usadas, 'modo': 'groq', 'tokens': (entrada, salida)}


def motores() -> list:
    """IA configuradas, en orden de preferencia: [(clave, nombre para mostrar, función)]."""
    lista = []
    if settings.ANTHROPIC_API_KEY:
        lista.append(('claude', f'Claude ({settings.ANTHROPIC_MODEL})', con_claude))
    if settings.GROQ_API_KEY:
        lista.append(('groq', f'Groq ({settings.GROQ_MODEL or GROQ_MODELOS[0]})', con_groq))
    if settings.GEMINI_API_KEY:
        lista.append(('gemini', f'Gemini ({settings.GEMINI_MODEL or GEMINI_MODELOS[0]})', con_gemini))
    return lista


def motor() -> tuple[str, str]:
    """Qué IA está activa: ('claude' | 'groq' | 'gemini' | 'basico', nombre para mostrar)."""
    lista = motores()
    return (lista[0][0], lista[0][1]) if lista else ('basico', 'modo básico')


def _por_que(error) -> str:
    if isinstance(error, ErrorGroq):
        if error.codigo == 429:
            return 'se llegó al límite gratuito de Groq por ahora; espera un minuto'
        if error.codigo in (401, 403):
            return 'la clave GROQ_API_KEY no es válida; revísala en el archivo .env'
        return f'Groq respondió error {error.codigo}: {error.mensaje[:160]}'
    if isinstance(error, ErrorGemini):
        if error.codigo == 403 and 'free tier' in error.mensaje.lower():
            return 'Google no habilitó el plan gratuito de Gemini en tu proyecto; usa GROQ_API_KEY (gratis)'

        if error.codigo == 429:
            return 'se llegó al límite gratuito de Gemini por ahora; espera un minuto'
        if error.codigo in (400, 401) and 'key' in str(error).lower():
            return 'la clave GEMINI_API_KEY no es válida o está incompleta; cópiala de nuevo con «Copy key»'
        if error.codigo == 403:
            return ('Google rechazó la clave (403). Crea una clave NUEVA en aistudio.google.com y cópiala completa. '
                    f'Mensaje de Google: {error.mensaje[:160]}')
        return f'Gemini respondió error {error.codigo}: {error.mensaje[:160]}'
    if isinstance(error, urllib.error.URLError):
        return 'no hay conexión a internet'
    return error.__class__.__name__


def _acciones(nombre, resultado) -> list:
    """Botones que la interfaz muestra bajo la respuesta (ej. abrir el borrador creado)."""
    if not isinstance(resultado, dict) or resultado.get('error'):
        return []
    if nombre == 'preparar_factura':
        return [{'texto': f'Revisar y emitir factura (${resultado["total"]:,.2f})', 'url': resultado['enlace']}]
    if nombre == 'consultar_factura':
        return [{'texto': f'Abrir {resultado["tipo"].lower()} {resultado["numero"]}', 'url': resultado['enlace']}]
    return []


# ---------------------------------------------------------------------
#  Modo básico (sin clave o sin internet)
# ---------------------------------------------------------------------
NUM = {'un': 1, 'una': 1, 'uno': 1, 'dos': 2, 'tres': 3, 'cuatro': 4, 'cinco': 5, 'seis': 6, 'siete': 7, 'ocho': 8,
       'nueve': 9, 'diez': 10}


def _dinero(v):
    return f'${v:,.2f}'


def modo_basico(usuario, texto: str) -> dict:
    t = H.normal(texto)
    hoy = timezone.localdate()

    def tiene(*palabras):
        return any(p in t for p in palabras)

    # Pide una factura pero no dice para quién: se pregunta, no se inventa nada
    if tiene('factura', 'facturar') and tiene('prepara', 'haz', 'hazme', 'crea', 'genera', 'emite', 'nueva factura') \
            and not re.search(r'\b(para|a)\s', t):
        return _r('Claro. ¿Para qué cliente y qué servicios? Escríbelo así:\n'
                  '«Factura para Luis Mora: 2 tour Mindo y 1 almuerzo»\n'
                  'Puedes usar el nombre o la cédula del cliente. Si es nuevo, regístralo primero en Clientes.')
    # Factura: "factura para Luis Mora 2 tour mindo"
    if tiene('factura', 'facturar', 'cobrale', 'vende') and re.search(r'\b(para|a)\s', t) and not re.search(r'factura\s+(n\S*\s*)?\d', t):
        return _factura_basica(usuario, texto)
    m = re.search(r'(\d{3}-\d{3}-\d{9}|factura\s+(n\S*\s*)?(\d+))', t)
    if m and tiene('factura', 'nota'):
        r = H.consultar_factura(m.group(1) if '-' in m.group(1) else m.group(3))
        if r.get('error'):
            return _r(r['error'])
        lineas = '\n'.join(f'• {l["servicio"]}: IVA {l["iva_pct"]:g} % ({l["por_que_ese_iva"]})' for l in r['lineas'])
        return _r(f'{r["tipo"]} {r["numero"]} del {r["fecha"]} a {r["cliente"]}: total {_dinero(r["total"])}, '
                  f'{r["estado"].lower()}, SRI: {r["sri"].lower()}. Saldo: {_dinero(r["saldo_pendiente"])}.\n{lineas}',
                  _acciones('consultar_factura', r), ['consultar_factura'])
    if tiene('declar', '104', 'impuesto', 'iva del mes', 'iva a pagar'):
        r = H.iva_a_declarar()
        return _r(f'IVA cobrado en {r["periodo"]}: {_dinero(r["iva_cobrado_total"])}.\n'
                  f'• Tarifa general: base {_dinero(r["ventas_tarifa_general"]["base"])}, IVA {_dinero(r["ventas_tarifa_general"]["iva"])}\n'
                  f'• IVA reducido (feriados): base {_dinero(r["ventas_iva_reducido_turismo"]["base"])}, IVA {_dinero(r["ventas_iva_reducido_turismo"]["iva"])}\n'
                  f'• Tarifa 0 %: base {_dinero(r["ventas_tarifa_0"]["base"])}\n{r["nota"]}', herramientas=['iva_a_declarar'])
    fecha_txt = _fecha_en_texto(t, hoy)
    if (tiene('iva') and (fecha_txt or tiene('hoy', 'feriado', 'cuanto', 'cual', 'tarifa', 'ahora', 'manana', 'hay'))) \
            or tiene('proximo feriado', 'cuando es el'):
        f = fecha_txt or (hoy + timedelta(days=1) if 'manana' in t else hoy)
        r = H.estado_iva(f.isoformat())
        if r['feriado_vigente']:
            fv = r['feriado_vigente']
            msg = (f'{_cuando(f, hoy)} rige el feriado de {fv["feriado"]} ({fv["decreto"]}): los servicios '
                   f'turísticos pagan IVA {fv["iva_turistico"]:g} % hasta el {fv["hasta"]}; lo demás, {r["iva_general"]:g} %.')
        else:
            msg = f'{_cuando(f, hoy)} el IVA es {r["iva_general"]:g} % para todo (no hay feriado con decreto).'
        if r['proximo_feriado']:
            p = r['proximo_feriado']
            msg += f' Próximo feriado: {p["feriado"]} desde el {p["desde"]} (faltan {p["dias_faltan"]} días), IVA turístico {p["iva_turistico"]:g} %.'
        else:
            msg += ' No hay próximos feriados con decreto registrados; cuando el Gobierno publique uno, se registra en «Feriados e IVA».'
        return _r(msg, herramientas=['estado_iva'])
    if tiene('feriado', 'decreto'):
        r = H.listar_feriados()
        if not r['decretos']:
            return _r('No hay decretos registrados este año. Regístralos en «Feriados e IVA».')
        return _r('Feriados con IVA reducido registrados:\n' + '\n'.join(
            f'• {d["feriado"]}: {d["desde"]} al {d["hasta"]} · IVA {d["iva"]:g} % ({d["decreto"]})' for d in r['decretos']),
            herramientas=['listar_feriados'])
    if tiene('debe', 'deuda', 'cartera', 'cobrar', 'moroso', 'pendiente de pago'):
        r = H.cartera()
        if not r['clientes']:
            return _r('Nadie te debe: no hay facturas a crédito pendientes.')
        return _r(f'Te deben {_dinero(r["total_por_cobrar"])} en total ({_dinero(r["vencido_mas_30_dias"])} vencido hace más de 30 días):\n'
                  + '\n'.join(f'• {c["cliente"]}: {_dinero(c["debe"])}' for c in r['clientes'][:8]), herramientas=['cartera'])
    if tiene('vend', 'venta', 'facture', 'factura', 'ingreso', 'gane'):
        desde = hoy if tiene('hoy') else hoy - timedelta(days=hoy.weekday()) if tiene('semana') else hoy.replace(day=1)
        r = H.resumen_ventas(desde.isoformat(), hoy.isoformat())
        top = H.ventas_por_servicio(desde.isoformat(), hoy.isoformat(), 3)['servicios']
        texto_top = ('\nMás vendidos: ' + ', '.join(f'{s["nombre"]} ({s["cantidad"]:g})' for s in top)) if top else ''
        return _r(f'Del {desde:%d/%m/%Y} al {hoy:%d/%m/%Y}: {r["facturas_emitidas"]} facturas por {_dinero(r["total_vendido"])} '
                  f'(IVA {_dinero(r["iva_total"])}).\n• Con IVA reducido de feriado: base {_dinero(r["base_iva_reducido_feriados"])}'
                  f'\n• Con tarifa general: base {_dinero(r["base_tarifa_general"])}'
                  f'\nLos turistas ahorraron {_dinero(r["ahorro_de_los_turistas_por_decreto"])} gracias al decreto.{texto_top}',
                  herramientas=['resumen_ventas', 'ventas_por_servicio'])
    if tiene('alerta', 'revisa', 'problema', 'pendiente', 'que hay', 'novedad'):
        r = H.alertas()
        return _r('Todo en orden: no hay alertas.' if not r['alertas'] else
                  'Esto requiere atención:\n' + '\n'.join(f'• {a}' for a in r['alertas']), herramientas=['alertas'])
    return _r('Puedo ayudarte con:\n• «¿Qué IVA rige hoy?» o «¿Cuándo es el próximo feriado?»\n'
              '• «¿Cuánto vendí este mes?»\n• «¿Quién me debe?»\n• «¿Cuánto IVA declaro este mes?»\n'
              '• «Factura para Luis Mora: 2 tour Mindo»\n• «Revisa si hay problemas»')


MESES_TXT = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre',
             'noviembre', 'diciembre']


def _fecha_en_texto(t, hoy):
    """Entiende «el 2 de noviembre», «2/11», «2026-11-02»."""
    from datetime import date
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', t)
    try:
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.search(r'\b(\d{1,2})\s+de\s+(' + '|'.join(MESES_TXT) + r')(?:\s+(?:de|del)\s+(\d{4}))?', t)
        if m:
            return date(int(m.group(3) or hoy.year), MESES_TXT.index(m.group(2)) + 1, int(m.group(1)))
        m = re.search(r'\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b', t)
        if m:
            return date(int(m.group(3) or hoy.year), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None
    return None


def _cuando(f, hoy):
    if f == hoy:
        return 'Hoy'
    if f == hoy + timedelta(days=1):
        return 'Mañana'
    return f'El {f:%d/%m/%Y}'


def _factura_basica(usuario, texto):
    t = H.normal(texto)
    m = re.search(r'\b(?:para|a)\s+(.+?)(?::|,|\s+de\s+|\s+por\s+|\s+(?:\d+|un|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s|\s*$)', t.strip() + ' ')
    nombre = m.group(1).strip() if m else ''
    if nombre in ('mi', 'mí', 'mi por favor', 'nosotros', 'el cliente', 'un cliente', 'alguien', ''):
        return _r('Claro. ¿Para qué cliente y qué servicios? Escríbelo así:\n'
                  '«Factura para Luis Mora: 2 tour Mindo y 1 almuerzo»\n'
                  'Puedes usar el nombre o la cédula del cliente. Si es nuevo, regístralo primero en Clientes.')
    clientes = H.buscar_clientes(nombre)['clientes'] if nombre else []
    if len(clientes) != 1:
        return _r(f'No encontré un único cliente llamado «{nombre}». Escribe su nombre completo o su cédula, '
                  'o regístralo primero en Clientes.' if nombre else 'Dime para qué cliente es la factura.')
    resto = t[m.end() - 1:] if m else t
    lineas = []
    for cantidad, nombre_serv in re.findall(r'(\d+(?:[.,]\d+)?|un|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+([a-z0-9 ]+?)(?=,| y |$|\d)', resto):
        encontrados = H.buscar_servicios(nombre_serv)['servicios']
        if encontrados:
            lineas.append({'servicio_id': encontrados[0]['id'],
                           'cantidad': NUM.get(cantidad) or float(cantidad.replace(',', '.'))})
    if not lineas:
        return _r(f'Encontré al cliente {clientes[0]["nombre"]}. ¿Qué servicios le facturo y cuántos? '
                  'Ejemplo: «Factura para Luis Mora: 2 tour Mindo y 1 habitación doble».')
    r = H.preparar_factura(clientes[0]['id'], lineas, _usuario=usuario)
    if r.get('error'):
        return _r(r['error'])
    detalle = '\n'.join(f'• {l["cantidad"]:g} × {l["servicio"]}: IVA {l["iva_pct"]:g} % ({l["motivo"]})' for l in r['lineas'])
    return _r(f'Preparé la factura para {r["cliente"]} por {_dinero(r["total"])} (IVA {_dinero(r["iva"])}):\n{detalle}\n'
              'Quedó en borrador: revísala y presiona «Emitir».', _acciones('preparar_factura', r),
              ['buscar_clientes', 'buscar_servicios', 'preparar_factura'])


def _r(texto, acciones=None, herramientas=None):
    return {'texto': texto, 'acciones': acciones or [], 'herramientas': herramientas or [], 'modo': 'basico',
            'tokens': (0, 0)}


# ---------------------------------------------------------------------
#  Entrada única
# ---------------------------------------------------------------------
def responder(usuario, historial: list, texto: str, pagina='') -> dict:
    inicio = time.monotonic()
    r, fallos = None, []
    for _, nombre, funcion in motores():  # si una IA falla, se prueba la siguiente configurada
        try:
            r = funcion(usuario, historial, texto, pagina)
            break
        except Exception as error:  # sin internet, clave inválida, límite de uso…
            fallos.append(f'{nombre.split(" ")[0]}: {_por_que(error)}')
    if r is None:
        r = modo_basico(usuario, texto)
        if fallos:
            r['aviso'] = 'No pude conectarme con la IA (' + '; '.join(fallos) + '); respondí en modo básico.'
    elif fallos:
        r['aviso'] = 'Aviso: ' + '; '.join(fallos)
    r['duracion_ms'] = int((time.monotonic() - inicio) * 1000)
    return r
