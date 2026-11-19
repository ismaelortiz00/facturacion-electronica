"""
RIDE: representación impresa del comprobante electrónico (PDF), con la clave de
acceso en código de barras Code 128, como pide el SRI.
"""
from facturacion.templatetags.formato import precio
from io import BytesIO

from reportlab.graphics.barcode import code128
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NEGRO = colors.HexColor('#111827')
GRIS = colors.HexColor('#e5e7eb')
P = ParagraphStyle('p', fontName='Helvetica', fontSize=8, leading=10, textColor=NEGRO)
PB = ParagraphStyle('pb', parent=P, fontName='Helvetica-Bold')
T = ParagraphStyle('t', parent=P, fontName='Helvetica-Bold', fontSize=12, leading=15)


def _p(texto, estilo=P):
    return Paragraph(str(texto).replace('&', '&amp;').replace('<', '&lt;'), estilo)


def _caja(filas, anchos):
    t = Table(filas, colWidths=anchos)
    t.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), 0.6, NEGRO), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                           ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2)]))
    return t


def generar(comprobante) -> bytes:
    e = comprobante.empresa
    doc = comprobante.documento
    numero = doc.numero
    salida = BytesIO()
    pdf = SimpleDocTemplate(salida, pagesize=A4, leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm, title=f'RIDE {numero}')
    h = []

    # Encabezado: emisor a la izquierda, datos del comprobante a la derecha
    emisor = [[_p(e.razon_social, T)], [_p(e.nombre_comercial or '')], [_p(f'Dirección matriz: {e.direccion}')],
              [_p(f'Obligado a llevar contabilidad: {"SI" if e.obligado_contabilidad else "NO"}')]]
    if e.contribuyente_especial:
        emisor.append([_p(f'Contribuyente especial n.º {e.contribuyente_especial}')])
    if e.agente_retencion:
        emisor.append([_p(f'Agente de retención, resolución n.º {e.agente_retencion}')])
    if e.regimen != 'general':
        emisor.append([_p(e.get_regimen_display().upper(), PB)])
    barra = code128.Code128(comprobante.clave_acceso, barHeight=11 * mm, barWidth=0.21 * mm, humanReadable=False)
    autorizado = comprobante.estado == 'autorizado'
    datos = [[_p(f'R.U.C.: {e.ruc}', PB)], [_p(comprobante.get_tipo_display().upper(), T)], [_p(f'No. {numero}', PB)],
             [_p('NÚMERO DE AUTORIZACIÓN', PB)], [_p(comprobante.numero_autorizacion or comprobante.clave_acceso)],
             [_p('FECHA Y HORA DE AUTORIZACIÓN: ' + (f'{comprobante.fecha_autorizacion:%d/%m/%Y %H:%M:%S}'
                                                     if comprobante.fecha_autorizacion else 'PENDIENTE'))],
             [_p(f'AMBIENTE: {"PRODUCCIÓN" if comprobante.ambiente == "2" else "PRUEBAS"}    EMISIÓN: NORMAL')],
             [_p('CLAVE DE ACCESO', PB)], [barra], [_p(comprobante.clave_acceso)]]
    if not autorizado:
        datos.insert(1, [_p(f'ESTADO: {comprobante.get_estado_display().upper()}', PB)])
    cab = Table([[_caja(emisor, [88 * mm]), _caja(datos, [96 * mm])]], colWidths=[90 * mm, 96 * mm])
    cab.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0)]))
    h += [cab, Spacer(1, 4 * mm)]

    c = doc.cliente
    filas_c = [[_p(f'Razón social / nombres: {c.nombre}'), _p(f'Identificación: {c.identificacion}')],
               [_p(f'Fecha de emisión: {doc.fecha:%d/%m/%Y}'), _p(f'Dirección: {c.direccion or "—"}')],
               [_p(f'Forma de pago: {doc.get_forma_pago_display()}'), _p(f'Correo: {c.correo or "—"}')]]
    if doc.modifica:
        filas_c.append([_p(f'Comprobante que se modifica: FACTURA {doc.modifica.numero} del '
                           f'{doc.modifica.fecha:%d/%m/%Y}'), _p(f'Razón: {doc.motivo}')])
    h += [_caja(filas_c, [120 * mm, 66 * mm]), Spacer(1, 4 * mm)]
    filas = [[_p(t, PB) for t in ('Código', 'Descripción', 'Cantidad', 'Precio unit.', 'Descuento', 'IVA %',
                                  'Precio total')]]
    for l in doc.lineas.select_related('servicio'):
        filas.append([_p(l.servicio.codigo), _p(l.descripcion), _p(f'{l.cantidad.normalize():f}'),
                      _p(precio(l.precio)), _p(f'{l.descuento:,.2f}'), _p(f'{l.porcentaje_iva.normalize():f}'),
                      _p(f'{l.subtotal:,.2f}')])
    anchos = [22 * mm, 66 * mm, 18 * mm, 22 * mm, 22 * mm, 14 * mm, 22 * mm]
    bases = {}
    for l in doc.lineas.all():
        bases[l.porcentaje_iva] = bases.get(l.porcentaje_iva, 0) + l.subtotal
    totales = [(f'SUBTOTAL {k.normalize():f} %', v) for k, v in sorted(bases.items(), reverse=True)]
    totales += [('TOTAL DESCUENTO', doc.descuento), ('SUBTOTAL SIN IMPUESTOS', doc.subtotal),
                ('IVA', doc.iva), ('VALOR TOTAL', doc.total)]
    tabla = Table(filas, colWidths=anchos, repeatRows=1)
    tabla.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.4, NEGRO), ('BACKGROUND', (0, 0), (-1, 0), GRIS),
                               ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    h += [tabla, Spacer(1, 3 * mm)]
    if doc.decreto:
        h.append(_p(f'IVA reducido para servicios turísticos aplicado por {doc.decreto.feriado} '
                    f'({doc.decreto.decreto}, del {doc.decreto.desde:%d/%m/%Y} al {doc.decreto.hasta:%d/%m/%Y}).', PB))
        h.append(Spacer(1, 3 * mm))
    tot = Table([[_p(n, PB), _p(f'{v:,.2f}')] for n, v in totales], colWidths=[46 * mm, 26 * mm], hAlign='RIGHT')
    tot.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.4, NEGRO), ('ALIGN', (1, 0), (1, -1), 'RIGHT')]))
    h.append(tot)
    pdf.build(h)
    return salida.getvalue()
