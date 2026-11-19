"""
HERRAMIENTAS DEL ASISTENTE DE IA
Son las acciones reales que la IA puede usar. Cada una llama a las mismas reglas del
negocio que las pantallas (facturacion.servicios), así que la IA no puede saltarse nada:
valida cédulas, calcula el IVA según el decreto y solo deja facturas en BORRADOR.
Emitir y anular siempre lo hace una persona con un botón.
"""
import unicodedata
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from facturacion import servicios as S
from facturacion.models import Cliente, Comprobante, DecretoFeriado, ErrorNegocio, Servicio


def _f(valor) -> float:
    return float(Decimal(valor or 0).quantize(Decimal('0.01')))


def _fecha(texto, defecto=None):
    if not texto:
        return defecto or timezone.localdate()
    try:
        return date.fromisoformat(str(texto)[:10])
    except ValueError:
        raise ErrorNegocio(f'La fecha "{texto}" no es válida (usa AAAA-MM-DD).')


def normal(texto) -> str:
    return unicodedata.normalize('NFD', str(texto or '')).encode('ascii', 'ignore').decode().lower()


# ---------------------------------------------------------------------
#  Implementaciones
# ---------------------------------------------------------------------
def estado_iva(fecha=None, **_):
    f = _fecha(fecha)
    e = S.estado_iva(f)
    return {'fecha': f.isoformat(), 'iva_general': _f(e['general']),
            'feriado_vigente': {'feriado': e['decreto'].feriado, 'decreto': e['decreto'].decreto,
                                'iva_turistico': _f(e['decreto'].porcentaje), 'desde': e['decreto'].desde.isoformat(),
                                'hasta': e['decreto'].hasta.isoformat()} if e['decreto'] else None,
            'proximo_feriado': {'feriado': e['proximo'].feriado, 'desde': e['proximo'].desde.isoformat(),
                                'hasta': e['proximo'].hasta.isoformat(), 'iva_turistico': _f(e['proximo'].porcentaje),
                                'dias_faltan': e['dias_para_proximo']} if e['proximo'] else None,
            'regla': 'Servicios turísticos con tarifa general pagan el IVA del decreto durante el feriado; '
                     'el resto paga la tarifa general.'}


def listar_feriados(anio=None, **_):
    anio = int(anio or timezone.localdate().year)
    return {'anio': anio, 'decretos': [{'feriado': d.feriado, 'decreto': d.decreto, 'desde': d.desde.isoformat(),
                                        'hasta': d.hasta.isoformat(), 'iva': _f(d.porcentaje)}
                                       for d in DecretoFeriado.objects.filter(desde__year=anio).order_by('desde')]}


def resumen_ventas(desde=None, hasta=None, **_):
    hoy = timezone.localdate()
    d, h = _fecha(desde, hoy.replace(day=1)), _fecha(hasta, hoy)
    r = S.resumen_ventas(d, h)
    n = r['neto']
    return {'desde': d.isoformat(), 'hasta': h.isoformat(), 'facturas_emitidas': r['facturas']['cantidad'],
            'notas_de_credito': r['notas']['cantidad'], 'base_tarifa_general': _f(n['general']),
            'base_iva_reducido_feriados': _f(n['reducida']), 'base_cero': _f(n['cero']), 'iva_total': _f(n['iva']),
            'iva_tarifa_general': _f(r['iva_general']), 'iva_reducido': _f(r['iva_reducida']),
            'total_vendido': _f(n['total']), 'ahorro_de_los_turistas_por_decreto': _f(r['ahorro_clientes'])}


def ventas_por_servicio(desde=None, hasta=None, limite=10, **_):
    hoy = timezone.localdate()
    filas = S.ventas_por_servicio(_fecha(desde, hoy.replace(day=1)), _fecha(hasta, hoy))[:int(limite or 10)]
    return {'servicios': [{'codigo': f['servicio'].codigo, 'nombre': f['servicio'].nombre,
                           'cantidad': _f(f['cantidad']), 'vendido_sin_iva': _f(f['base'])} for f in filas]}


def iva_a_declarar(anio=None, mes=None, **_):
    hoy = timezone.localdate()
    anio, mes = int(anio or hoy.year), int(mes or hoy.month)
    desde = date(anio, mes, 1)
    hasta = (desde.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    r = S.resumen_ventas(desde, hasta)
    n = r['neto']
    return {'periodo': f'{mes:02d}/{anio}', 'ventas_tarifa_general': {'base': _f(n['general']), 'iva': _f(r['iva_general'])},
            'ventas_iva_reducido_turismo': {'base': _f(n['reducida']), 'iva': _f(r['iva_reducida'])},
            'ventas_tarifa_0': {'base': _f(n['cero'])}, 'iva_cobrado_total': _f(n['iva']),
            'notas_de_credito_restadas': _f(r['notas']['total']),
            'nota': 'Es el IVA cobrado en ventas; el valor a pagar lo define el contador restando el crédito tributario.'}


def cartera(cliente=None, **_):
    c = S.cartera()
    clientes = c['clientes']
    if cliente:
        t = normal(cliente)
        clientes = [x for x in clientes if t in normal(x['cliente'].nombre) or x['cliente'].identificacion.startswith(cliente)]
    return {'total_por_cobrar': _f(c['totales']['total']), 'vencido_mas_30_dias': _f(c['totales']['d60'] + c['totales']['d90'] + c['totales']['mas90']),
            'clientes': [{'cliente': x['cliente'].nombre, 'identificacion': x['cliente'].identificacion,
                          'debe': _f(x['total']),
                          'facturas': [{'numero': f['factura'].numero, 'saldo': _f(f['saldo']), 'dias_vencida': f['dias']}
                                       for f in x['facturas']]} for x in clientes[:15]]}


def buscar_clientes(texto='', **_):
    t = (texto or '').strip()
    qs = Cliente.objects.filter(activo=True)
    for palabra in t.split():
        qs = qs.filter(Q(nombre__icontains=palabra) | Q(identificacion__startswith=palabra) | Q(correo__icontains=palabra))
    return {'clientes': [{'id': c.id, 'nombre': c.nombre, 'identificacion': c.identificacion,
                          'tipo': c.get_tipo_identificacion_display(), 'correo': c.correo, 'debe': _f(c.saldo())}
                         for c in qs[:8]]}


def buscar_servicios(texto='', **_):
    hoy = timezone.localdate()
    qs = Servicio.objects.filter(activo=True)
    palabras = [p for p in normal(texto).split() if len(p) > 2]
    todos = list(qs)
    if palabras:
        todos = [s for s in todos if any(p in normal(s.nombre + ' ' + s.codigo) for p in palabras)]
        todos.sort(key=lambda s: -sum(p in normal(s.nombre + ' ' + s.codigo) for p in palabras))
    return {'servicios': [{'id': s.id, 'codigo': s.codigo, 'nombre': s.nombre, 'precio_sin_iva': _f(s.precio),
                           'turistico': s.turistico, 'iva_hoy': _f(S.iva_para(s, hoy)[0])} for s in todos[:10]]}


def consultar_factura(numero='', **_):
    n = (numero or '').strip()
    qs = Comprobante.objects.exclude(estado='borrador')
    qs = qs.order_by('tipo')  # con el mismo número, primero la factura y luego la nota de crédito
    if n.isdigit():
        comp = qs.filter(secuencial=int(n)).first()
    else:
        partes = n.split('-')
        comp = qs.filter(secuencial=int(partes[-1])).first() if partes[-1].isdigit() else None
    if comp is None:
        return {'error': f'No encontré la factura {numero}.'}
    ce = getattr(comp, 'electronico', None) if hasattr(comp, 'electronico') else None
    return {'tipo': comp.get_tipo_display(), 'numero': comp.numero, 'fecha': comp.fecha.isoformat(),
            'cliente': comp.cliente.nombre, 'estado': comp.get_estado_display(), 'total': _f(comp.total),
            'iva': _f(comp.iva), 'saldo_pendiente': _f(comp.saldo()), 'forma_pago': comp.get_forma_pago_display(),
            'sri': ce.get_estado_display() if ce else 'sin enviar',
            'lineas': [{'servicio': l.descripcion, 'cantidad': _f(l.cantidad), 'subtotal': _f(l.subtotal),
                        'iva_pct': _f(l.porcentaje_iva), 'por_que_ese_iva': l.motivo_iva} for l in comp.lineas.all()],
            'enlace': reverse('facturacion:factura', args=[comp.pk])}


def alertas(**_):
    return {'alertas': [a['texto'] for a in S.alertas()]}


def crear_cliente(tipo_identificacion, identificacion, nombre, correo='', telefono='', direccion='', pais='Ecuador',
                  _usuario=None, **_):
    ok, mensaje = S.revisar_identificacion(tipo_identificacion, identificacion)
    if not ok:
        return {'error': mensaje}
    existente = Cliente.objects.filter(identificacion=identificacion).first()
    if existente:
        return {'id': existente.id, 'nombre': existente.nombre, 'nota': 'El cliente ya existía.'}
    c = Cliente.objects.create(tipo_identificacion=tipo_identificacion, identificacion=identificacion, nombre=nombre[:250],
                               correo=correo or '', telefono=telefono or '', direccion=direccion or '', pais=pais or '')
    S.registrar(_usuario, 'Creó cliente', str(c), por_ia=True)
    return {'id': c.id, 'nombre': c.nombre, 'creado': True}


def preparar_factura(cliente_id, lineas, fecha=None, forma_pago='efectivo', plazo_dias=0, observacion='',
                     _usuario=None, **_):
    try:
        comp = S.guardar(_usuario, {'tipo': 'factura', 'fecha': _fecha(fecha), 'cliente_id': cliente_id,
                                    'forma_pago': forma_pago or 'efectivo', 'plazo_dias': plazo_dias or 0,
                                    'observacion': observacion}, [
            {'servicio_id': l.get('servicio_id'), 'cantidad': l.get('cantidad', 1), 'precio': l.get('precio'),
             'descuento': l.get('descuento'), 'descripcion': l.get('descripcion')} for l in (lineas or [])], origen='ia')
    except ErrorNegocio as error:
        return {'error': error.message}
    if not comp.lineas.exists():
        comp.delete()
        return {'error': 'No hay servicios válidos en la factura.'}
    S.registrar(_usuario, 'Preparó factura (borrador)', f'{comp.cliente.nombre} · {comp.total:,.2f}', por_ia=True)
    return {'borrador_id': comp.id, 'cliente': comp.cliente.nombre, 'fecha': comp.fecha.isoformat(),
            'subtotal': _f(comp.subtotal), 'iva': _f(comp.iva), 'total': _f(comp.total),
            'feriado_aplicado': comp.decreto.feriado if comp.decreto else None,
            'lineas': [{'servicio': l.descripcion, 'cantidad': _f(l.cantidad), 'iva_pct': _f(l.porcentaje_iva),
                        'motivo': l.motivo_iva} for l in comp.lineas.all()],
            'enlace': reverse('facturacion:factura', args=[comp.pk]),
            'importante': 'Quedó en BORRADOR. El usuario debe revisarla y presionar «Emitir» para enviarla al SRI.'}


# ---------------------------------------------------------------------
#  Definición para Claude (nombre, descripción y parámetros)
# ---------------------------------------------------------------------
FECHA = {'type': 'string', 'description': 'Fecha AAAA-MM-DD'}
HERRAMIENTAS = [
    {'name': 'estado_iva', 'description': 'IVA que rige en una fecha: tarifa general, si hay feriado con decreto '
     '(IVA turístico reducido) y cuándo es el próximo feriado.', 'input_schema': {'type': 'object', 'properties': {'fecha': FECHA}}},
    {'name': 'listar_feriados', 'description': 'Decretos de feriado registrados en un año con sus fechas y su IVA.',
     'input_schema': {'type': 'object', 'properties': {'anio': {'type': 'integer'}}}},
    {'name': 'resumen_ventas', 'description': 'Ventas entre dos fechas (facturas menos notas de crédito) separadas por '
     'tarifa: general, reducida de feriado y 0 %; IVA y ahorro de los turistas. Por defecto, el mes actual.',
     'input_schema': {'type': 'object', 'properties': {'desde': FECHA, 'hasta': FECHA}}},
    {'name': 'ventas_por_servicio', 'description': 'Servicios más vendidos entre dos fechas.',
     'input_schema': {'type': 'object', 'properties': {'desde': FECHA, 'hasta': FECHA, 'limite': {'type': 'integer'}}}},
    {'name': 'iva_a_declarar', 'description': 'IVA cobrado en un mes, separado por tarifa, para la declaración.',
     'input_schema': {'type': 'object', 'properties': {'anio': {'type': 'integer'}, 'mes': {'type': 'integer'}}}},
    {'name': 'cartera', 'description': 'Quién debe y cuánto (facturas a crédito con saldo). Opcional: filtrar por cliente.',
     'input_schema': {'type': 'object', 'properties': {'cliente': {'type': 'string'}}}},
    {'name': 'buscar_clientes', 'description': 'Busca clientes por nombre, cédula, RUC, pasaporte o correo.',
     'input_schema': {'type': 'object', 'properties': {'texto': {'type': 'string'}}, 'required': ['texto']}},
    {'name': 'buscar_servicios', 'description': 'Busca servicios por nombre o código; devuelve precio, si es turístico '
     'y el IVA que pagaría hoy.', 'input_schema': {'type': 'object', 'properties': {'texto': {'type': 'string'}}}},
    {'name': 'consultar_factura', 'description': 'Detalle de una factura o nota de crédito por su número (ej. 001-001-000000012 '
     'o 12): líneas, IVA de cada una y por qué, saldo y estado en el SRI.',
     'input_schema': {'type': 'object', 'properties': {'numero': {'type': 'string'}}, 'required': ['numero']}},
    {'name': 'alertas', 'description': 'Problemas y avisos del negocio: feriados próximos, rechazos del SRI, borradores '
     'olvidados, clientes morosos, firma por vencer.', 'input_schema': {'type': 'object', 'properties': {}}},
    {'name': 'crear_cliente', 'description': 'Registra un cliente nuevo (valida cédula o RUC). Úsalo solo con datos que '
     'dio el usuario.', 'input_schema': {'type': 'object', 'properties': {
        'tipo_identificacion': {'type': 'string', 'enum': ['cedula', 'ruc', 'pasaporte', 'consumidor']},
        'identificacion': {'type': 'string'}, 'nombre': {'type': 'string'}, 'correo': {'type': 'string'},
        'telefono': {'type': 'string'}, 'direccion': {'type': 'string'}, 'pais': {'type': 'string'}},
        'required': ['tipo_identificacion', 'identificacion', 'nombre']}},
    {'name': 'preparar_factura', 'description': 'Crea una factura en BORRADOR (no la emite). El sistema calcula el IVA '
     'de cada línea según la fecha y los decretos. Necesitas el id del cliente y los id de los servicios.',
     'input_schema': {'type': 'object', 'properties': {
        'cliente_id': {'type': 'integer'}, 'fecha': FECHA,
        'forma_pago': {'type': 'string', 'enum': ['efectivo', 'transferencia', 'tarjeta_credito', 'tarjeta_debito', 'credito']},
        'plazo_dias': {'type': 'integer'}, 'observacion': {'type': 'string'},
        'lineas': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'servicio_id': {'type': 'integer'}, 'cantidad': {'type': 'number'},
            'precio': {'type': 'number', 'description': 'Solo si el usuario pidió otro precio'},
            'descuento': {'type': 'number'}, 'descripcion': {'type': 'string'}}, 'required': ['servicio_id']}}},
        'required': ['cliente_id', 'lineas']}},
]

FUNCIONES = {'estado_iva': estado_iva, 'listar_feriados': listar_feriados, 'resumen_ventas': resumen_ventas,
             'ventas_por_servicio': ventas_por_servicio, 'iva_a_declarar': iva_a_declarar, 'cartera': cartera,
             'buscar_clientes': buscar_clientes, 'buscar_servicios': buscar_servicios,
             'consultar_factura': consultar_factura, 'alertas': alertas, 'crear_cliente': crear_cliente,
             'preparar_factura': preparar_factura}


def ejecutar(nombre: str, argumentos: dict, usuario) -> dict:
    funcion = FUNCIONES.get(nombre)
    if funcion is None:
        return {'error': f'Herramienta desconocida: {nombre}'}
    import inspect
    aceptados = inspect.signature(funcion).parameters
    # Las IA a veces mandan campos vacíos (null) o de más: se ignoran para que rijan los valores por defecto
    limpios = {k: v for k, v in (argumentos or {}).items() if v not in (None, '') and k in aceptados and k != '_usuario'}
    try:
        return funcion(**limpios, _usuario=usuario)
    except ErrorNegocio as error:
        return {'error': error.message}
    except (TypeError, ValueError) as error:
        return {'error': f'Datos incompletos o inválidos: {error}'}
