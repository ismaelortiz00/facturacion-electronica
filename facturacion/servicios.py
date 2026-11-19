"""
REGLAS DEL NEGOCIO
Todo lo que cambia datos pasa por aquí (pantallas y asistente de IA usan las mismas
funciones), así las reglas se cumplen siempre, venga de donde venga la orden.
"""
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from cuentas.validadores import revisar_cedula

from .models import (CERO, Actividad, Aplicacion, Cliente, Cobro, Comprobante, DecretoFeriado, Empresa, ErrorNegocio,
                     Linea, Secuencia, Servicio, TasaIva)


def redondear(valor) -> Decimal:
    return Decimal(valor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def numero(texto, campo='valor') -> Decimal:
    try:
        valor = Decimal(str(texto if texto not in (None, '') else '0').strip().replace(',', ''))
    except InvalidOperation:
        valor = None
    if valor is None or not valor.is_finite() or abs(valor) >= Decimal('1e10'):
        raise ErrorNegocio(f'«{texto}» no es un número válido en el campo {campo}.')
    return valor


def registrar(usuario, accion, detalle='', por_ia=False):
    Actividad.objects.create(usuario=usuario if getattr(usuario, 'pk', None) else None, accion=accion[:60],
                             detalle=detalle[:400], por_ia=por_ia)


# ---------------------------------------------------------------------
#  Identificación (Registro Civil y SRI)
# ---------------------------------------------------------------------
def revisar_ruc(ruc: str) -> tuple[bool, str]:
    """13 dígitos, provincia válida, tercer dígito 0-5 (persona natural, cédula válida), 6 o 9; establecimiento ≠ 000."""
    if not re.fullmatch(r'\d{13}', ruc or ''):
        return False, 'El RUC debe tener 13 números.'
    provincia = int(ruc[:2])
    if not (1 <= provincia <= 24 or provincia == 30):
        return False, 'Los dos primeros dígitos del RUC no corresponden a una provincia.'
    if ruc[10:] == '000':
        return False, 'Los tres últimos dígitos (establecimiento) no pueden ser 000.'
    tercero = int(ruc[2])
    if tercero <= 5:
        ok, mensaje = revisar_cedula(ruc[:10])
        if not ok:
            return False, 'RUC de persona natural con cédula inválida: ' + mensaje
    elif tercero not in (6, 9):
        return False, 'El tercer dígito del RUC debe ser 0-5, 6 o 9.'
    return True, 'RUC válido.'


def revisar_identificacion(tipo: str, valor: str) -> tuple[bool, str]:
    valor = (valor or '').strip()
    if tipo == 'cedula':
        return revisar_cedula(valor)
    if tipo == 'ruc':
        return revisar_ruc(valor)
    if tipo == 'consumidor':
        return valor == '9999999999999', 'El consumidor final se identifica con 9999999999999.'
    if tipo == 'pasaporte':
        ok = bool(re.fullmatch(r'[A-Za-z0-9]{3,20}', valor))
        return ok, 'Pasaporte válido.' if ok else 'El pasaporte debe tener de 3 a 20 letras o números.'
    return False, 'Tipo de identificación no válido.'


# ---------------------------------------------------------------------
#  IVA: el corazón del sistema
# ---------------------------------------------------------------------
def tasa_general(fecha: date) -> Decimal:
    tasa = TasaIva.objects.filter(desde__lte=fecha).order_by('-desde').first()
    if tasa is None:
        raise ErrorNegocio('No hay tarifa general de IVA registrada para esa fecha.')
    return tasa.porcentaje


def decreto_vigente(fecha: date):
    return DecretoFeriado.objects.filter(desde__lte=fecha, hasta__gte=fecha).order_by('-desde').first()


def iva_para(servicio: Servicio, fecha: date) -> tuple[Decimal, DecretoFeriado | None]:
    """
    Devuelve (porcentaje, decreto aplicado):
      · servicio turístico con tarifa general en un día de feriado con decreto → IVA reducido del decreto
      · cualquier otro servicio con tarifa general → tarifa general vigente a la fecha
      · 0 %, exento o no objeto → 0
    """
    if servicio.tarifa_iva != 'general':
        return CERO, None
    if servicio.turistico:
        d = decreto_vigente(fecha)
        if d:
            return d.porcentaje, d
    return tasa_general(fecha), None


def estado_iva(fecha: date) -> dict:
    """Para mostrar en pantalla y para el asistente: qué IVA rige hoy y cuándo es el próximo feriado."""
    d = decreto_vigente(fecha)
    proximo = DecretoFeriado.objects.filter(desde__gt=fecha).order_by('desde').first()
    return {'fecha': fecha, 'general': tasa_general(fecha), 'decreto': d,
            'proximo': proximo, 'dias_para_proximo': (proximo.desde - fecha).days if proximo else None}


# ---------------------------------------------------------------------
#  Facturas y notas de crédito
# ---------------------------------------------------------------------
def _id(valor) -> int:
    """Convierte un id que llega de un formulario o de la IA en entero (0 si no es válido)."""
    texto = str(valor or '').strip()
    return int(texto) if texto.isdigit() else 0


def siguiente(tipo, establecimiento, punto) -> int:
    s, _ = Secuencia.objects.select_for_update().get_or_create(tipo=tipo, establecimiento=establecimiento, punto=punto)
    s.ultimo += 1
    s.save(update_fields=['ultimo'])
    return s.ultimo


@transaction.atomic
def guardar(usuario, datos: dict, lineas: list[dict], comp: Comprobante | None = None, origen='manual') -> Comprobante:
    """
    Guarda (o actualiza) un borrador y calcula el IVA de cada línea.
    datos  = {tipo, fecha, cliente_id, forma_pago, plazo_dias, modifica_id, motivo, observacion}
    lineas = [{servicio_id, descripcion, cantidad, precio, descuento}]
    """
    empresa = Empresa.actual()
    if empresa is None:
        raise ErrorNegocio('Primero registra los datos de la empresa emisora (Configuración → Empresa).')
    if comp is None:
        comp = Comprobante(creado_por=usuario, origen=origen, establecimiento=empresa.establecimiento,
                           punto_emision=empresa.punto_emision)
    elif comp.estado != 'borrador':
        raise ErrorNegocio('Solo se modifican documentos en borrador.')
    comp.tipo = datos.get('tipo') or 'factura'
    comp.fecha = datos.get('fecha') or timezone.localdate()
    cliente = Cliente.objects.filter(pk=_id(datos.get('cliente_id')), activo=True).first()
    if cliente is None:
        raise ErrorNegocio('Elige el cliente.')
    comp.cliente = cliente
    comp.forma_pago = datos.get('forma_pago') or 'efectivo'
    comp.plazo_dias = min(_id(datos.get('plazo_dias')), 365) if comp.forma_pago == 'credito' else 0
    comp.motivo = (datos.get('motivo') or '').strip()[:300]
    comp.observacion = (datos.get('observacion') or '').strip()[:300]
    comp.modifica = None
    if comp.tipo == 'nota_credito':
        comp.modifica = Comprobante.objects.filter(pk=_id(datos.get('modifica_id')), tipo='factura').first()
        comp.forma_pago = 'credito' if comp.modifica and comp.modifica.forma_pago == 'credito' else 'efectivo'
    comp.save()

    comp.lineas.all().delete()
    originales = {}
    if comp.modifica:  # la nota de crédito usa el IVA con que se facturó (aunque hoy rija otro)
        originales = {l.servicio_id: l for l in comp.modifica.lineas.all()}
    servicios = {s.id: s for s in Servicio.objects.filter(id__in=[_id(l.get('servicio_id')) for l in lineas])}
    bases = {'general': CERO, 'reducida': CERO, 'cero': CERO}
    grupos, descuento, decreto = {}, CERO, None
    for i, l in enumerate(lineas, 1):
        if not l.get('servicio_id'):
            continue
        s = servicios.get(_id(l['servicio_id']))
        if s is None:
            raise ErrorNegocio(f'Línea {i}: el servicio no existe.')
        cantidad = numero(l.get('cantidad') or 1, 'cantidad')
        precio = numero(l.get('precio') if l.get('precio') not in (None, '') else s.precio, 'precio')
        desc = redondear(numero(l.get('descuento'), 'descuento'))
        if cantidad <= 0 or precio < 0:
            raise ErrorNegocio(f'Línea {i}: la cantidad debe ser mayor que cero y el precio no puede ser negativo.')
        bruto = redondear(cantidad * precio)
        if desc < 0 or desc > bruto:
            raise ErrorNegocio(f'Línea {i}: el descuento no puede superar el valor de la línea.')
        base = bruto - desc
        if comp.modifica:
            o = originales.get(s.id)
            if o is None:
                raise ErrorNegocio(f'Línea {i}: {s.nombre} no está en la factura {comp.modifica.numero}.')
            tarifa, pct, dec = o.tarifa, o.porcentaje_iva, o.decreto
        else:
            tarifa = s.tarifa_iva
            pct, dec = iva_para(s, comp.fecha)
        Linea.objects.create(comprobante=comp, servicio=s, descripcion=(l.get('descripcion') or s.nombre)[:300],
                             cantidad=cantidad, precio=precio, descuento=desc, tarifa=tarifa, porcentaje_iva=pct,
                             subtotal=base, iva=redondear(base * pct / 100), decreto=dec)
        grupos[pct] = grupos.get(pct, CERO) + base
        if pct == 0:
            bases['cero'] += base
        elif dec:
            bases['reducida'] += base
            decreto = dec
        else:
            bases['general'] += base
        descuento += desc
    comp.base_general, comp.base_reducida, comp.base_cero = bases['general'], bases['reducida'], bases['cero']
    comp.subtotal = sum(bases.values(), CERO)
    comp.descuento = descuento
    comp.iva = sum((redondear(b * p / 100) for p, b in grupos.items()), CERO)  # se redondea por tarifa, como el SRI
    comp.total = comp.subtotal + comp.iva
    comp.decreto = decreto
    comp.save()
    return comp


@transaction.atomic
def emitir(comp: Comprobante, usuario, por_ia=False) -> Comprobante:
    comp = Comprobante.objects.select_for_update().get(pk=comp.pk)
    if comp.estado != 'borrador':
        raise ErrorNegocio('El documento ya fue emitido.')
    if not comp.lineas.exists():
        raise ErrorNegocio('Agrega al menos un servicio.')
    if comp.fecha > timezone.localdate():
        raise ErrorNegocio('La fecha no puede ser futura: el SRI no autoriza comprobantes con fecha posterior a hoy.')
    if comp.tipo == 'factura':
        if comp.cliente.tipo_identificacion == 'consumidor' and comp.total > 50:
            raise ErrorNegocio('Una factura a consumidor final no puede pasar de USD 50: pide los datos del cliente.')
        if comp.forma_pago == 'credito':
            if comp.cliente.tipo_identificacion == 'consumidor':
                raise ErrorNegocio('No se da crédito a consumidor final.')
            comp.vencimiento = comp.fecha + timedelta(days=comp.plazo_dias or 30)
            comp.plazo_dias = comp.plazo_dias or 30
    else:
        f = comp.modifica
        if f is None or f.estado != 'emitido' or f.cliente_id != comp.cliente_id:
            raise ErrorNegocio('La nota de crédito debe referirse a una factura emitida del mismo cliente.')
        if len(comp.motivo) < 5:
            raise ErrorNegocio('Escribe el motivo de la nota de crédito (al menos 5 letras).')
        if comp.fecha < f.fecha:
            raise ErrorNegocio('La nota de crédito no puede tener fecha anterior a la factura.')
        # El tope es el total de la factura menos las notas anteriores (aunque ya esté pagada: sería una devolución)
        disponible = f.total - (f.notas.filter(estado='emitido').aggregate(s=Sum('total'))['s'] or CERO)
        if comp.total > disponible:
            raise ErrorNegocio(f'La nota de crédito ({comp.total:,.2f}) supera lo que queda de la factura ({disponible:,.2f}).')
    comp.secuencial = siguiente(comp.tipo, comp.establecimiento, comp.punto_emision)
    comp.estado = 'emitido'
    comp.save()
    registrar(usuario, f'Emitió {comp.get_tipo_display().lower()}', f'{comp.numero} · {comp.cliente.nombre} · {comp.total:,.2f}',
              por_ia)
    return comp


@transaction.atomic
def anular(comp: Comprobante, usuario, motivo: str) -> Comprobante:
    comp = Comprobante.objects.select_for_update().get(pk=comp.pk)
    if comp.estado != 'emitido':
        raise ErrorNegocio('Solo se anulan documentos emitidos.')
    if len((motivo or '').strip()) < 5:
        raise ErrorNegocio('Escribe el motivo de la anulación (al menos 5 letras).')
    if comp.aplicaciones.filter(cobro__estado='emitido').exists():
        raise ErrorNegocio('La factura tiene cobros: anula primero esos cobros.')
    if comp.notas.filter(estado='emitido').exists():
        raise ErrorNegocio('La factura tiene notas de crédito vigentes: anúlalas primero.')
    comp.estado = 'anulado'
    comp.motivo_anulacion = motivo.strip()[:300]
    comp.save()
    registrar(usuario, f'Anuló {comp.get_tipo_display().lower()}', f'{comp.numero}: {motivo}')
    return comp


@transaction.atomic
def corregir(comp: Comprobante, usuario, motivo: str) -> Comprobante:
    """Anula y deja una copia en borrador para arreglarla (para errores de digitación)."""
    anular(comp, usuario, motivo)
    copia = Comprobante.objects.create(tipo=comp.tipo, fecha=timezone.localdate(), cliente=comp.cliente,
                                       modifica=comp.modifica, motivo=comp.motivo, forma_pago=comp.forma_pago,
                                       plazo_dias=comp.plazo_dias, observacion=comp.observacion, creado_por=usuario,
                                       establecimiento=comp.establecimiento, punto_emision=comp.punto_emision)
    lineas = [{'servicio_id': l.servicio_id, 'descripcion': l.descripcion, 'cantidad': l.cantidad, 'precio': l.precio,
               'descuento': l.descuento} for l in comp.lineas.all()]
    return guardar(usuario, {'tipo': copia.tipo, 'fecha': copia.fecha, 'cliente_id': copia.cliente_id,
                             'forma_pago': copia.forma_pago, 'plazo_dias': copia.plazo_dias,
                             'modifica_id': copia.modifica_id, 'motivo': copia.motivo,
                             'observacion': copia.observacion}, lineas, copia)


# ---------------------------------------------------------------------
#  Cobros
# ---------------------------------------------------------------------
@transaction.atomic
def registrar_cobro(usuario, datos: dict, aplicaciones: list[dict], por_ia=False) -> Cobro:
    """datos = {fecha, cliente_id, forma, referencia}; aplicaciones = [{factura_id, valor}]"""
    cliente = Cliente.objects.filter(pk=_id(datos.get('cliente_id'))).first()
    if cliente is None:
        raise ErrorNegocio('Elige el cliente.')
    filas = []
    for a in aplicaciones:
        v = redondear(numero(a.get('valor')))
        if v <= 0:
            continue
        f = Comprobante.objects.select_for_update().filter(pk=_id(a.get('factura_id')), cliente=cliente, tipo='factura',
                                                            estado='emitido').first()
        if f is None:
            raise ErrorNegocio('Una de las facturas no es de este cliente.')
        if v > f.saldo():
            raise ErrorNegocio(f'Factura {f.numero}: el valor ({v:,.2f}) supera lo que debe ({f.saldo():,.2f}).')
        if (datos.get('fecha') or timezone.localdate()) < f.fecha:
            raise ErrorNegocio(f'El cobro no puede tener fecha anterior a la factura {f.numero} ({f.fecha:%d/%m/%Y}).')
        filas.append((f, v))
    if not filas:
        raise ErrorNegocio('Escribe cuánto se cobra de al menos una factura.')
    total = sum((v for _, v in filas), CERO)
    ultimo = Cobro.objects.select_for_update().order_by('-numero').first()
    cobro = Cobro.objects.create(numero=(ultimo.numero + 1) if ultimo else 1, fecha=datos.get('fecha') or timezone.localdate(),
                                 cliente=cliente, valor=total, forma=datos.get('forma') or 'efectivo',
                                 referencia=(datos.get('referencia') or '')[:80], creado_por=usuario)
    for f, v in filas:
        Aplicacion.objects.create(cobro=cobro, factura=f, valor=v)
    registrar(usuario, 'Registró cobro', f'{cobro} · {cliente.nombre} · {total:,.2f}', por_ia)
    return cobro


@transaction.atomic
def anular_cobro(cobro: Cobro, usuario, motivo: str) -> Cobro:
    if cobro.estado != 'emitido':
        raise ErrorNegocio('El cobro ya está anulado.')
    if len((motivo or '').strip()) < 5:
        raise ErrorNegocio('Escribe el motivo.')
    cobro.estado = 'anulado'
    cobro.motivo_anulacion = motivo.strip()[:300]
    cobro.save()
    registrar(usuario, 'Anuló cobro', f'{cobro}: {motivo}')
    return cobro


def pendientes(cliente) -> list[Comprobante]:
    return [f for f in Comprobante.objects.filter(cliente=cliente, tipo='factura', estado='emitido', forma_pago='credito')
            .order_by('fecha') if f.saldo() > 0]


# ---------------------------------------------------------------------
#  REPORTES
# ---------------------------------------------------------------------
def resumen_ventas(desde: date, hasta: date) -> dict:
    """Facturas menos notas de crédito, separadas por tarifa: lo que se declara en el formulario de IVA."""
    docs = Comprobante.objects.filter(estado='emitido', fecha__gte=desde, fecha__lte=hasta)
    r = {}
    for tipo in ('factura', 'nota_credito'):
        a = docs.filter(tipo=tipo).aggregate(general=Sum('base_general'), reducida=Sum('base_reducida'),
                                             cero=Sum('base_cero'), iva=Sum('iva'), total=Sum('total'))
        r[tipo] = {k: v or CERO for k, v in a.items()}
        r[tipo]['cantidad'] = docs.filter(tipo=tipo).count()
    f, n = r['factura'], r['nota_credito']
    iva_general = ahorro = CERO
    for l in Linea.objects.filter(comprobante__in=docs).select_related('comprobante'):
        signo = 1 if l.comprobante.tipo == 'factura' else -1
        if l.porcentaje_iva and not l.decreto_id:
            iva_general += signo * l.iva
        elif l.decreto_id:  # lo que el turista dejó de pagar gracias al decreto
            ahorro += signo * redondear(l.subtotal * (tasa_general(l.comprobante.fecha) - l.porcentaje_iva) / 100)
    neto = {k: f[k] - n[k] for k in ('general', 'reducida', 'cero', 'iva', 'total')}
    return {'desde': desde, 'hasta': hasta, 'facturas': f, 'notas': n, 'neto': neto,
            'iva_general': iva_general, 'iva_reducida': neto['iva'] - iva_general, 'ahorro_clientes': ahorro}


def ventas_por_dia(desde: date, hasta: date) -> list[dict]:
    dias = {}
    for c in Comprobante.objects.filter(estado='emitido', fecha__gte=desde, fecha__lte=hasta):
        signo = 1 if c.tipo == 'factura' else -1
        d = dias.setdefault(c.fecha, {'fecha': c.fecha, 'total': CERO, 'reducida': CERO, 'general': CERO,
                                      'feriado': bool(decreto_vigente(c.fecha))})
        d['total'] += signo * c.total
        d['reducida'] += signo * c.base_reducida
        d['general'] += signo * c.base_general
    return sorted(dias.values(), key=lambda x: x['fecha'])


def ventas_por_servicio(desde: date, hasta: date) -> list[dict]:
    filas = {}
    for l in Linea.objects.filter(comprobante__estado='emitido', comprobante__fecha__gte=desde,
                                  comprobante__fecha__lte=hasta).select_related('servicio', 'comprobante'):
        signo = 1 if l.comprobante.tipo == 'factura' else -1
        f = filas.setdefault(l.servicio_id, {'servicio': l.servicio, 'cantidad': CERO, 'base': CERO, 'iva': CERO})
        f['cantidad'] += signo * l.cantidad
        f['base'] += signo * l.subtotal
        f['iva'] += signo * l.iva
    return sorted(filas.values(), key=lambda x: -x['base'])


def cartera(corte: date | None = None) -> dict:
    corte = corte or timezone.localdate()
    tramos = ['por_vencer', 'd30', 'd60', 'd90', 'mas90']
    clientes, tot = {}, {t: CERO for t in tramos + ['total']}
    for f in Comprobante.objects.filter(tipo='factura', estado='emitido', forma_pago='credito',
                                        fecha__lte=corte).select_related('cliente'):
        s = f.saldo()
        if s <= 0:
            continue
        dias = (corte - (f.vencimiento or f.fecha)).days
        t = 'por_vencer' if dias <= 0 else 'd30' if dias <= 30 else 'd60' if dias <= 60 else 'd90' if dias <= 90 else 'mas90'
        c = clientes.setdefault(f.cliente_id, {'cliente': f.cliente, 'facturas': [], 'total': CERO})
        c['facturas'].append({'factura': f, 'saldo': s, 'dias': max(dias, 0), 'tramo': t})
        c['total'] += s
        tot[t] += s
        tot['total'] += s
    return {'corte': corte, 'clientes': sorted(clientes.values(), key=lambda c: -c['total']), 'totales': tot}


# ---------------------------------------------------------------------
#  ALERTAS (las usa el panel y el asistente de IA)
# ---------------------------------------------------------------------
def alertas() -> list[dict]:
    hoy = timezone.localdate()
    salida = []
    e = estado_iva(hoy)
    if e['decreto']:
        salida.append({'nivel': 'info', 'texto': f'Hoy rige el IVA del {e["decreto"].porcentaje.normalize():f} % para '
                       f'servicios turísticos ({e["decreto"].feriado}, hasta el {e["decreto"].hasta:%d/%m}). '
                       'El sistema lo aplica solo.'})
    elif e['proximo'] and e['dias_para_proximo'] <= 7:
        salida.append({'nivel': 'info', 'texto': f'En {e["dias_para_proximo"]} día(s) empieza el feriado de '
                       f'{e["proximo"].feriado}: los servicios turísticos pasarán al IVA del '
                       f'{e["proximo"].porcentaje.normalize():f} % automáticamente.'})
    if not DecretoFeriado.objects.filter(hasta__gte=hoy).exists():
        salida.append({'nivel': 'aviso', 'texto': 'No hay decretos de feriado registrados hacia adelante. '
                       'Cuando el Gobierno publique uno, regístralo en «Feriados e IVA».'})
    viejos = Comprobante.objects.filter(estado='borrador', creado_en__lt=timezone.now() - timedelta(days=2)).count()
    if viejos:
        salida.append({'nivel': 'aviso', 'texto': f'{viejos} borrador(es) de más de 2 días sin emitir.', 'enlace': 'borradores'})
    from sri.models import ComprobanteElectronico, FirmaElectronica
    problemas = ComprobanteElectronico.objects.filter(estado__in=['devuelto', 'no_autorizado']).count()
    if problemas:
        salida.append({'nivel': 'error', 'texto': f'{problemas} comprobante(s) rechazado(s) por el SRI: revisa el motivo.',
                       'enlace': 'sri'})
    sin_enviar = Comprobante.objects.filter(estado='emitido', electronico__isnull=True).count()
    en_espera = ComprobanteElectronico.objects.filter(estado__in=['firmado', 'error', 'recibido']).count()
    if sin_enviar + en_espera:
        salida.append({'nivel': 'aviso', 'texto': f'{sin_enviar + en_espera} documento(s) sin autorización del SRI todavía.',
                       'enlace': 'sri'})
    firma = FirmaElectronica.objects.first()
    if firma is None:
        salida.append({'nivel': 'aviso', 'texto': 'Falta subir la firma electrónica (.p12) para enviar las facturas al SRI.',
                       'enlace': 'firma'})
    elif firma.vence and (firma.vence.date() - hoy).days <= 30:
        salida.append({'nivel': 'error', 'texto': f'La firma electrónica vence el {firma.vence:%d/%m/%Y}: renuévala.',
                       'enlace': 'firma'})
    vencidas = [c for c in cartera(hoy)['clientes'] if any(f['dias'] > 30 for f in c['facturas'])]
    if vencidas:
        nombres = ', '.join(c['cliente'].nombre for c in vencidas[:3])
        salida.append({'nivel': 'aviso', 'texto': f'Clientes con facturas vencidas hace más de 30 días: {nombres}'
                       f'{"…" if len(vencidas) > 3 else ""}', 'enlace': 'cartera'})
    return salida
