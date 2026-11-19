"""
Generación del XML de los comprobantes electrónicos según la ficha técnica del SRI
(esquema offline): factura 1.1.0 y nota de crédito 1.1.0.
El código de porcentaje "8" es el del IVA reducido para turismo en feriados.
"""
import random
from decimal import ROUND_HALF_UP, Decimal

from lxml import etree

CERO = Decimal('0.00')

TIPO_IDENTIFICACION = {'ruc': '04', 'cedula': '05', 'pasaporte': '06', 'consumidor': '07'}


# ---------------------------------------------------------------------
#  Clave de acceso: 48 dígitos + dígito verificador (módulo 11)
# ---------------------------------------------------------------------
def digito_modulo11(numero: str) -> str:
    factores = [2, 3, 4, 5, 6, 7]
    suma = sum(int(d) * factores[i % 6] for i, d in enumerate(reversed(numero)))
    dv = 11 - (suma % 11)
    return '0' if dv == 11 else '1' if dv == 10 else str(dv)


def clave_acceso(fecha, tipo, ruc, ambiente, establecimiento, punto, secuencial, codigo_numerico=None) -> str:
    codigo_numerico = codigo_numerico or f'{random.SystemRandom().randint(0, 99999999):08d}'
    base = (f'{fecha:%d%m%Y}{tipo}{ruc}{ambiente}{establecimiento}{punto}{secuencial:09d}'
            f'{codigo_numerico}1')
    assert len(base) == 48, base
    return base + digito_modulo11(base)


def revisar_clave(clave: str) -> bool:
    return len(clave) == 49 and clave.isdigit() and digito_modulo11(clave[:48]) == clave[48]


# ---------------------------------------------------------------------
#  Ayudas
# ---------------------------------------------------------------------
def d2(x) -> str:
    return f'{Decimal(x):.2f}'


def d6(x) -> str:
    """Hasta 6 decimales, mínimo 2: 2.500000 → 2.50 · 1.123456 → 1.123456"""
    entero, decimales = f'{Decimal(x):.6f}'.split('.')
    return f'{entero}.{decimales.rstrip("0").ljust(2, "0")}'


def codigo_porcentaje(tarifa: str, porcentaje) -> str:
    if tarifa == '0':
        return '0'
    if tarifa == 'no_objeto':
        return '6'
    if tarifa == 'exento':
        return '7'
    return {Decimal('12'): '2', Decimal('14'): '3', Decimal('15'): '4', Decimal('5'): '5',
            Decimal('8'): '8', Decimal('13'): '10'}.get(Decimal(porcentaje).normalize(), '4')


def sub(padre, etiqueta, texto=None, **atributos):
    nodo = etree.SubElement(padre, etiqueta, **atributos)
    if texto is not None:
        nodo.text = str(texto)
    return nodo


def limpiar(texto, largo=300):
    """El SRI rechaza saltos de línea y espacios dobles en los campos de texto."""
    return ' '.join(str(texto or '').split())[:largo] or 'N/A'


def info_tributaria(raiz, empresa, tipo, clave, ambiente, establecimiento, punto, secuencial):
    it = sub(raiz, 'infoTributaria')
    sub(it, 'ambiente', ambiente)
    sub(it, 'tipoEmision', '1')
    sub(it, 'razonSocial', limpiar(empresa.razon_social))
    if empresa.nombre_comercial:
        sub(it, 'nombreComercial', limpiar(empresa.nombre_comercial))
    sub(it, 'ruc', empresa.ruc)
    sub(it, 'claveAcceso', clave)
    sub(it, 'codDoc', tipo)
    sub(it, 'estab', establecimiento)
    sub(it, 'ptoEmi', punto)
    sub(it, 'secuencial', f'{secuencial:09d}')
    sub(it, 'dirMatriz', limpiar(empresa.direccion))
    if empresa.agente_retencion:
        sub(it, 'agenteRetencion', empresa.agente_retencion.lstrip('0') or '1')
    if empresa.regimen == 'rimpe_emprendedor':
        sub(it, 'contribuyenteRimpe', 'CONTRIBUYENTE RÉGIMEN RIMPE')
    elif empresa.regimen == 'rimpe_popular':
        sub(it, 'contribuyenteRimpe', 'CONTRIBUYENTE NEGOCIO POPULAR - RÉGIMEN RIMPE')


def comprador(nodo, tercero, prefijo='Comprador'):
    sub(nodo, f'tipoIdentificacion{prefijo}', TIPO_IDENTIFICACION.get(tercero.tipo_identificacion, '05'))
    sub(nodo, f'razonSocial{prefijo}', limpiar(tercero.nombre))
    sub(nodo, f'identificacion{prefijo}', tercero.identificacion)


def especial_y_contabilidad(nodo, empresa):
    if empresa.contribuyente_especial:
        sub(nodo, 'contribuyenteEspecial', empresa.contribuyente_especial)
    sub(nodo, 'obligadoContabilidad', 'SI' if empresa.obligado_contabilidad else 'NO')


def grupos_iva(lineas):
    grupos = {}
    for l in lineas:
        cod = codigo_porcentaje(l.tarifa, l.porcentaje_iva)
        g = grupos.setdefault(cod, {'base': CERO, 'tarifa': l.porcentaje_iva})
        g['base'] += l.subtotal
    for cod, g in grupos.items():
        g['valor'] = (g['base'] * g['tarifa'] / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return grupos


def info_adicional(raiz, campos):
    campos = [(n, v) for n, v in campos if v]
    if campos:
        ia = sub(raiz, 'infoAdicional')
        for nombre, valor in campos:
            sub(ia, 'campoAdicional', limpiar(valor, 300), nombre=nombre)


FORMAS_PAGO_SRI = {'efectivo': '01', 'tarjeta_debito': '16', 'tarjeta_credito': '19', 'transferencia': '20', 'credito': '20'}


def forma_pago_sri(venta):
    """Tabla 24 de la ficha técnica: 01 sin sistema financiero, 16 débito, 19 crédito, 20 otros con sistema financiero."""
    return FORMAS_PAGO_SRI.get(venta.forma_pago, '20')


# ---------------------------------------------------------------------
#  FACTURA 1.1.0
# ---------------------------------------------------------------------
def xml_factura(venta, clave, ambiente) -> etree._Element:
    e, c = venta.empresa, venta.cliente
    lineas = list(venta.lineas.select_related('servicio'))
    raiz = etree.Element('factura', id='comprobante', version='1.1.0')
    info_tributaria(raiz, e, '01', clave, ambiente, venta.establecimiento, venta.punto_emision, venta.secuencial)
    inf = sub(raiz, 'infoFactura')
    sub(inf, 'fechaEmision', f'{venta.fecha:%d/%m/%Y}')
    sub(inf, 'dirEstablecimiento', limpiar(e.direccion))
    especial_y_contabilidad(inf, e)
    comprador(inf, c)
    if c.direccion:
        sub(inf, 'direccionComprador', limpiar(c.direccion))
    sub(inf, 'totalSinImpuestos', d2(venta.subtotal))
    sub(inf, 'totalDescuento', d2(venta.descuento))
    tci = sub(inf, 'totalConImpuestos')
    for cod, g in grupos_iva(lineas).items():
        ti = sub(tci, 'totalImpuesto')
        sub(ti, 'codigo', '2')
        sub(ti, 'codigoPorcentaje', cod)
        sub(ti, 'baseImponible', d2(g['base']))
        sub(ti, 'valor', d2(g['valor']))
    sub(inf, 'propina', '0.00')
    sub(inf, 'importeTotal', d2(venta.total))
    sub(inf, 'moneda', 'DOLAR')
    pagos = sub(inf, 'pagos')
    pago = sub(pagos, 'pago')
    sub(pago, 'formaPago', forma_pago_sri(venta))
    sub(pago, 'total', d2(venta.total))
    if venta.forma_pago == 'credito':
        sub(pago, 'plazo', str(venta.plazo_dias))
        sub(pago, 'unidadTiempo', 'dias')
    detalles(raiz, lineas)
    decreto = venta.decreto
    info_adicional(raiz, [('Correo', c.correo), ('Teléfono', c.telefono),
                          ('IVA turístico', f'{decreto.feriado} - {decreto.decreto}' if decreto else ''),
                          ('Observación', venta.observacion)])
    return raiz


def detalles(raiz, lineas, etiqueta_codigo='codigoPrincipal'):
    dets = sub(raiz, 'detalles')
    for l in lineas:
        d = sub(dets, 'detalle')
        sub(d, etiqueta_codigo, limpiar(l.servicio.codigo, 25))
        sub(d, 'descripcion', limpiar(l.descripcion))
        sub(d, 'cantidad', d6(l.cantidad))
        sub(d, 'precioUnitario', d6(l.precio))
        sub(d, 'descuento', d2(l.descuento))
        sub(d, 'precioTotalSinImpuesto', d2(l.subtotal))
        imps = sub(d, 'impuestos')
        i = sub(imps, 'impuesto')
        sub(i, 'codigo', '2')
        sub(i, 'codigoPorcentaje', codigo_porcentaje(l.tarifa, l.porcentaje_iva))
        sub(i, 'tarifa', d2(l.porcentaje_iva))
        sub(i, 'baseImponible', d2(l.subtotal))
        sub(i, 'valor', d2(l.iva))


# ---------------------------------------------------------------------
#  NOTA DE CRÉDITO 1.1.0
# ---------------------------------------------------------------------
def xml_nota_credito(venta, clave, ambiente) -> etree._Element:
    e, c, f = venta.empresa, venta.cliente, venta.modifica
    lineas = list(venta.lineas.select_related('servicio'))
    raiz = etree.Element('notaCredito', id='comprobante', version='1.1.0')
    info_tributaria(raiz, e, '04', clave, ambiente, venta.establecimiento, venta.punto_emision, venta.secuencial)
    inf = sub(raiz, 'infoNotaCredito')
    sub(inf, 'fechaEmision', f'{venta.fecha:%d/%m/%Y}')
    sub(inf, 'dirEstablecimiento', limpiar(e.direccion))
    comprador(inf, c)
    especial_y_contabilidad(inf, e)
    sub(inf, 'codDocModificado', '01')
    sub(inf, 'numDocModificado', f.numero)
    sub(inf, 'fechaEmisionDocSustento', f'{f.fecha:%d/%m/%Y}')
    sub(inf, 'totalSinImpuestos', d2(venta.subtotal))
    sub(inf, 'valorModificacion', d2(venta.total))
    sub(inf, 'moneda', 'DOLAR')
    tci = sub(inf, 'totalConImpuestos')
    for cod, g in grupos_iva(lineas).items():
        ti = sub(tci, 'totalImpuesto')
        sub(ti, 'codigo', '2')
        sub(ti, 'codigoPorcentaje', cod)
        sub(ti, 'baseImponible', d2(g['base']))
        sub(ti, 'valor', d2(g['valor']))
    sub(inf, 'motivo', limpiar(venta.motivo))
    detalles(raiz, lineas, 'codigoInterno')
    info_adicional(raiz, [('Correo', c.correo)])
    return raiz


