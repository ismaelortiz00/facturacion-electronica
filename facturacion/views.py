"""Pantallas del sistema de facturación turística."""
import json
from datetime import date, timedelta
from decimal import Decimal
from functools import wraps
from io import BytesIO

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import graficos, servicios
from .forms import ClienteForm, DecretoForm, EmpleadoForm, EmpresaForm, ServicioForm, TasaForm
from .models import (CERO, Actividad, Cliente, Cobro, Comprobante, DecretoFeriado, Empleado, Empresa, ErrorNegocio,
                     Servicio, TasaIva)

MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre',
         'noviembre', 'diciembre']


# ---------------------------------------------------------------------
#  Acceso: empleados habilitados; algunas pantallas solo para el administrador
# ---------------------------------------------------------------------
def empleado_de(usuario):
    emp = Empleado.objects.filter(usuario=usuario).first()
    if emp is None:
        # El primer usuario del sistema (o un superusuario) queda como administrador
        # (si ya existe un superusuario, él es el administrador: nadie más se vuelve admin al registrarse)
        es_primero = (not Empleado.objects.filter(rol='admin', activo=True).exists()
                      and not get_user_model().objects.filter(is_superuser=True).exclude(pk=usuario.pk).exists())
        emp = Empleado.objects.create(usuario=usuario, rol='admin' if (es_primero or usuario.is_superuser) else 'vendedor',
                                      activo=es_primero or usuario.is_superuser)
    return emp


def acceso(solo_admin=False):
    def decorador(vista):
        @login_required
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            emp = empleado_de(request.user)
            if not emp.activo:
                return render(request, 'facturacion/espera.html', status=403)
            if solo_admin and not emp.es_admin:
                messages.error(request, 'Esa opción es solo para el administrador.')
                return redirect('facturacion:inicio')
            request.empleado = emp
            request.empresa = Empresa.actual()
            if request.empresa is None and vista.__name__ != 'empresa':
                if emp.es_admin:
                    messages.info(request, 'Primero registra los datos de tu negocio (salen en cada factura).')
                    return redirect('facturacion:empresa')
                return render(request, 'facturacion/espera.html', {'sin_empresa': True}, status=403)
            return vista(request, *args, **kwargs)
        return envoltura
    return decorador


def volver_seguro(request, valor, defecto):
    """Solo permite volver a direcciones de este mismo sistema (evita redirecciones a sitios externos)."""
    from django.utils.http import url_has_allowed_host_and_scheme
    if valor and valor.startswith('/') and url_has_allowed_host_and_scheme(valor, allowed_hosts={request.get_host()}):
        return valor
    return reverse(defecto)


def fecha_param(request, nombre, defecto):
    try:
        return date.fromisoformat(request.GET.get(nombre, ''))
    except ValueError:
        return defecto


def rango_mes(request):
    hoy = timezone.localdate()
    return fecha_param(request, 'desde', hoy.replace(day=1)), fecha_param(request, 'hasta', hoy)


def excel(nombre, titulo, encabezados, filas, columnas_dinero=()):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = nombre[:31]
    ws.append([titulo])
    ws['A1'].font = Font(bold=True, size=13)
    ws.append([])
    ws.append(encabezados)
    for c in ws[3]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='1F6A49')
    for f in filas:
        ws.append([float(v) if isinstance(v, Decimal) else v for v in f])
    for col in columnas_dinero:
        for fila in ws.iter_rows(min_row=4, min_col=col, max_col=col):
            fila[0].number_format = '#,##0.00'
    for i, col in enumerate(ws.columns, 1):
        ws.column_dimensions[ws.cell(row=3, column=i).column_letter].width = min(max(
            max(len(str(c.value or '')) for c in col) + 2, 10), 50)
    b = BytesIO()
    wb.save(b)
    r = HttpResponse(b.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    r['Content-Disposition'] = f'attachment; filename="{nombre}.xlsx"'
    return r


# ---------------------------------------------------------------------
#  Panel
# ---------------------------------------------------------------------
@acceso()
def inicio(request):
    hoy = timezone.localdate()
    desde = hoy.replace(day=1)
    resumen = servicios.resumen_ventas(desde, hoy)
    dias = servicios.ventas_por_dia(desde, hoy)
    return render(request, 'facturacion/inicio.html', {
        'hoy': hoy, 'mes': MESES[hoy.month - 1], 'resumen': resumen, 'alertas': servicios.alertas(),
        'grafico': graficos.barras_diarias(desde, hoy, dias), 'por_cobrar': servicios.cartera(hoy)['totales']['total'],
        'ultimas': Comprobante.objects.select_related('cliente').exclude(estado='borrador')[:6],
        'decretos': DecretoFeriado.objects.filter(hasta__gte=hoy).order_by('desde')[:4],
    })


# ---------------------------------------------------------------------
#  Catálogos: clientes y servicios
# ---------------------------------------------------------------------
@acceso()
def clientes(request):
    q = request.GET.get('q', '').strip()
    qs = Cliente.objects.all()
    if q:
        qs = qs.filter(Q(nombre__icontains=q) | Q(identificacion__startswith=q) | Q(correo__icontains=q))
    return render(request, 'facturacion/clientes.html', {'clientes': qs[:500], 'q': q})


@acceso()
def cliente_form(request, pk=None):
    obj = get_object_or_404(Cliente, pk=pk) if pk else None
    # Los valores iniciales solo sirven al crear: al editar mandan los datos guardados del cliente
    inicial = {} if obj else {'tipo_identificacion': request.GET.get('tipo', 'cedula'), 'identificacion': request.GET.get('id', '')}
    form = ClienteForm(request.POST or None, instance=obj, initial=inicial)
    if request.method == 'POST' and form.is_valid():
        c = form.save()
        servicios.registrar(request.user, 'Guardó cliente', str(c))
        messages.success(request, f'Cliente {c.nombre} guardado.')
        if request.GET.get('volver'):  # viene de la factura: vuelve con el cliente ya elegido
            destino = volver_seguro(request, request.GET['volver'], 'facturacion:clientes')
            return redirect(f'{destino}{"&" if "?" in destino else "?"}cliente={c.pk}')
        return redirect('facturacion:clientes')
    return render(request, 'facturacion/form_simple.html', {
        'form': form, 'titulo': f'Editar {obj.nombre}' if obj else 'Nuevo cliente',
        'ayuda': 'La cédula y el RUC se validan con el algoritmo del Registro Civil y del SRI. A los turistas extranjeros '
                 'regístralos con su pasaporte.', 'volver': reverse('facturacion:clientes')})


@acceso()
def servicios_lista(request):
    q = request.GET.get('q', '').strip()
    qs = Servicio.objects.all()
    if q:
        qs = qs.filter(Q(nombre__icontains=q) | Q(codigo__icontains=q))
    hoy = timezone.localdate()
    filas = [{'s': s, 'iva_hoy': servicios.iva_para(s, hoy)[0]} for s in qs[:500]]
    return render(request, 'facturacion/servicios.html', {'filas': filas, 'q': q, 'estado': servicios.estado_iva(hoy)})


@acceso(solo_admin=True)
def servicio_form(request, pk=None):
    obj = get_object_or_404(Servicio, pk=pk) if pk else None
    form = ServicioForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        s = form.save()
        servicios.registrar(request.user, 'Guardó servicio', str(s))
        messages.success(request, f'Servicio {s.nombre} guardado.')
        return redirect('facturacion:servicios')
    return render(request, 'facturacion/form_simple.html', {
        'form': form, 'titulo': f'Editar {obj.nombre}' if obj else 'Nuevo servicio',
        'ayuda': 'Marca «es servicio turístico» en alojamiento, alimentos, tours, transporte turístico y agencias: '
                 'esos pagan el IVA reducido en los feriados con decreto.', 'volver': reverse('facturacion:servicios')})


# ---------------------------------------------------------------------
#  Feriados e IVA
# ---------------------------------------------------------------------
@acceso()
def feriados(request):
    hoy = timezone.localdate()
    dform, tform = DecretoForm(prefix='d'), TasaForm(prefix='t')
    if request.method == 'POST':
        if not request.empleado.es_admin:
            messages.error(request, 'Solo el administrador registra decretos.')
            return redirect('facturacion:feriados')
        if request.POST.get('accion') == 'decreto':
            dform = DecretoForm(request.POST, prefix='d')
            if dform.is_valid():
                d = dform.save()
                servicios.registrar(request.user, 'Registró decreto', str(d))
                messages.success(request, f'Decreto registrado: del {d.desde:%d/%m} al {d.hasta:%d/%m} los servicios '
                                          f'turísticos se facturarán con IVA {d.porcentaje.normalize():f} %.')
                return redirect('facturacion:feriados')
        elif request.POST.get('accion') == 'tasa':
            tform = TasaForm(request.POST, prefix='t')
            if tform.is_valid():
                tform.save()
                messages.success(request, 'Tarifa general registrada.')
                return redirect('facturacion:feriados')
        elif request.POST.get('accion') == 'borrar':
            d = get_object_or_404(DecretoFeriado, pk=request.POST.get('id'))
            if d.comprobantes.exists() or d.desde <= hoy:
                messages.error(request, 'Ese decreto ya se usó o ya empezó: no se puede borrar.')
            else:
                servicios.registrar(request.user, 'Borró decreto', str(d))
                d.delete()
                messages.success(request, 'Decreto borrado.')
            return redirect('facturacion:feriados')
    return render(request, 'facturacion/feriados.html', {
        'estado': servicios.estado_iva(hoy), 'decretos': DecretoFeriado.objects.all(), 'tasas': TasaIva.objects.all(),
        'dform': dform, 'tform': tform, 'hoy': hoy})


# ---------------------------------------------------------------------
#  Facturas y notas de crédito
# ---------------------------------------------------------------------
@acceso()
def facturas(request):
    desde, hasta = fecha_param(request, 'desde', timezone.localdate() - timedelta(days=60)), fecha_param(
        request, 'hasta', timezone.localdate())
    qs = Comprobante.objects.filter(fecha__gte=desde, fecha__lte=hasta).select_related('cliente', 'decreto', 'electronico')
    tipo, estado, q = request.GET.get('tipo', ''), request.GET.get('estado', ''), request.GET.get('q', '').strip()
    if tipo:
        qs = qs.filter(tipo=tipo)
    if estado:
        qs = qs.filter(estado=estado)
    if q:
        qs = qs.filter(Q(cliente__nombre__icontains=q) | Q(cliente__identificacion__startswith=q))
    return render(request, 'facturacion/facturas.html', {'facturas': qs[:500], 'desde': desde, 'hasta': hasta,
                                                         'tipo': tipo, 'estado': estado, 'q': q,
                                                         'borradores': Comprobante.objects.filter(estado='borrador').count()})


def _lineas_post(request):
    campos = ['servicio_id', 'descripcion', 'cantidad', 'precio', 'descuento']
    listas = {c: request.POST.getlist(c) for c in campos}
    n = len(listas['servicio_id'])
    return [{c: (listas[c][i] if i < len(listas[c]) else '') for c in campos} for i in range(n)]


@acceso()
def factura_form(request, pk=None):
    from types import SimpleNamespace
    comp = get_object_or_404(Comprobante, pk=pk) if pk else None
    if comp and comp.estado != 'borrador':
        return redirect('facturacion:factura', pk=comp.pk)
    hoy = timezone.localdate()
    reenviar = None  # si hay un error, se vuelve a mostrar lo que el usuario escribió
    if request.method == 'POST':
        lineas_post = _lineas_post(request)
        try:
            datos = {k: request.POST.get(k) for k in ('tipo', 'cliente_id', 'forma_pago', 'plazo_dias', 'modifica_id',
                                                      'motivo', 'observacion')}
            try:
                datos['fecha'] = date.fromisoformat(request.POST.get('fecha', ''))
            except ValueError:
                raise ErrorNegocio('La fecha no es válida.')
            if not (datos.get('plazo_dias') or '0').isdigit():
                raise ErrorNegocio('El plazo debe ser un número de días.')
            comp = servicios.guardar(request.user, datos, lineas_post, comp)
            if request.POST.get('accion') == 'emitir':
                comp = servicios.emitir(comp, request.user)
                messages.success(request, f'{comp} emitida por {comp.total:,.2f}.')
                _enviar_sri(request, comp)
            else:
                messages.success(request, 'Borrador guardado.')
            return redirect('facturacion:factura', pk=comp.pk)
        except ErrorNegocio as error:
            messages.error(request, error.message)
            if comp and comp.pk and Comprobante.objects.filter(pk=comp.pk).exists():
                return redirect('facturacion:factura_editar', pk=comp.pk)
            comp = None
            reenviar = {'post': request.POST, 'lineas': [SimpleNamespace(
                servicio_id=int(l['servicio_id']) if str(l['servicio_id']).isdigit() else None,
                descripcion=l['descripcion'], cantidad=l['cantidad'], precio=l['precio'], descuento=l['descuento'])
                for l in lineas_post if l['servicio_id'] or l['cantidad'] or l['precio']]}
    tipo = (reenviar and reenviar['post'].get('tipo')) or (comp.tipo if comp else request.GET.get('tipo', 'factura'))
    if comp:
        modifica = comp.modifica
    else:
        modifica_id = (reenviar and reenviar['post'].get('modifica_id')) or request.GET.get('modifica') or 0
        modifica = Comprobante.objects.filter(pk=modifica_id if str(modifica_id).isdigit() else 0, tipo='factura').first()
    if reenviar:
        p = reenviar['post']
        v = SimpleNamespace(fecha=p.get('fecha', ''), forma_pago=p.get('forma_pago', 'efectivo'), plazo_dias=p.get('plazo_dias') or 30,
                            observacion=p.get('observacion', ''), motivo=p.get('motivo', ''), cliente_id=p.get('cliente_id', ''))
        lineas = reenviar['lineas']
    else:
        v = SimpleNamespace(fecha=(comp.fecha if comp else hoy).isoformat(), forma_pago=comp.forma_pago if comp else 'efectivo',
                            plazo_dias=(comp.plazo_dias if comp and comp.plazo_dias else 30),
                            observacion=comp.observacion if comp else '', motivo=comp.motivo if comp else '',
                            cliente_id=comp.cliente_id if comp else (modifica.cliente_id if modifica else request.GET.get('cliente', '')))
        lineas = list(comp.lineas.select_related('servicio')) if comp else (list(modifica.lineas.all()) if modifica else [])
    return render(request, 'facturacion/factura_form.html', {
        'comp': comp, 'tipo': tipo, 'modifica': modifica, 'lineas': lineas or [None, None], 'v': v,
        'cliente_inicial': str(v.cliente_id or ''),
        'clientes': Cliente.objects.filter(activo=True),
        'servicios': Servicio.objects.filter(activo=True),
        'facturas': Comprobante.objects.filter(tipo='factura', estado='emitido').select_related('cliente')[:300],
        'formas': Comprobante.FORMAS_PAGO,
        'tasas': {'generales': [[t.desde.isoformat(), float(t.porcentaje)] for t in TasaIva.objects.order_by('desde')],
                  'decretos': [[d.desde.isoformat(), d.hasta.isoformat(), float(d.porcentaje), d.feriado]
                               for d in DecretoFeriado.objects.all()]},
    })


def _enviar_sri(request, comp):
    from sri.servicios import emitir_electronico
    ce, mensaje = emitir_electronico(comp)
    if mensaje:
        (messages.success if ce and ce.estado == 'autorizado' else messages.warning)(request, mensaje)


@acceso()
def factura(request, pk):
    comp = get_object_or_404(Comprobante.objects.select_related('cliente', 'modifica', 'decreto', 'creado_por'), pk=pk)
    from sri.models import ComprobanteElectronico
    ce = ComprobanteElectronico.objects.filter(comprobante=comp).first()
    return render(request, 'facturacion/factura.html', {
        'comp': comp, 'lineas': comp.lineas.select_related('servicio', 'decreto'), 'ce': ce,
        'cobros': comp.aplicaciones.select_related('cobro'), 'notas': comp.notas.all(), 'saldo': comp.saldo()})


@require_POST
@acceso()
def factura_accion(request, pk):
    comp = get_object_or_404(Comprobante, pk=pk)
    accion = request.POST.get('accion')
    try:
        if accion == 'emitir':
            comp = servicios.emitir(comp, request.user)
            messages.success(request, f'{comp} emitida.')
            _enviar_sri(request, comp)
        elif accion == 'eliminar' and comp.estado == 'borrador':
            comp.delete()
            messages.success(request, 'Borrador eliminado.')
            return redirect('facturacion:facturas')
        elif accion == 'anular':
            if not request.empleado.es_admin:
                raise ErrorNegocio('Solo el administrador anula facturas.')
            servicios.anular(comp, request.user, request.POST.get('motivo', ''))
            messages.success(request, f'{comp} anulada. Si estaba autorizada, anúlala también en el portal del SRI.')
        elif accion == 'corregir':
            if not request.empleado.es_admin:
                raise ErrorNegocio('Solo el administrador corrige facturas emitidas.')
            copia = servicios.corregir(comp, request.user, request.POST.get('motivo', ''))
            messages.success(request, f'{comp} anulada. Corrige el borrador y emítelo de nuevo.')
            return redirect('facturacion:factura_editar', pk=copia.pk)
        else:
            raise Http404
    except ErrorNegocio as error:
        messages.error(request, error.message)
    return redirect('facturacion:factura', pk=comp.pk)


@acceso()
def calcular(request):
    """Vista previa del IVA (JSON) para el formulario: usa exactamente la misma regla que al guardar."""
    try:
        fecha = date.fromisoformat(request.GET.get('fecha', ''))
    except ValueError:
        fecha = timezone.localdate()
    e = servicios.estado_iva(fecha)
    return JsonResponse({'general': float(e['general']),
                         'decreto': {'feriado': e['decreto'].feriado, 'decreto': e['decreto'].decreto,
                                     'porcentaje': float(e['decreto'].porcentaje)} if e['decreto'] else None})


# ---------------------------------------------------------------------
#  Cobros
# ---------------------------------------------------------------------
@acceso()
def cobros(request):
    desde, hasta = fecha_param(request, 'desde', timezone.localdate() - timedelta(days=60)), fecha_param(
        request, 'hasta', timezone.localdate())
    qs = Cobro.objects.filter(fecha__gte=desde, fecha__lte=hasta).select_related('cliente', 'creado_por')
    return render(request, 'facturacion/cobros.html', {'cobros': qs, 'desde': desde, 'hasta': hasta,
                                                       'total': sum((c.valor for c in qs if c.estado == 'emitido'), CERO)})


@acceso()
def cobro_form(request):
    if request.method == 'POST':
        try:
            aplicaciones = [{'factura_id': f, 'valor': v} for f, v in
                            zip(request.POST.getlist('factura_id'), request.POST.getlist('valor'))]
            datos = {k: request.POST.get(k) for k in ('cliente_id', 'forma', 'referencia')}
            datos['fecha'] = date.fromisoformat(request.POST.get('fecha', ''))
            cobro = servicios.registrar_cobro(request.user, datos, aplicaciones)
            messages.success(request, f'{cobro} registrado por {cobro.valor:,.2f}.')
            return redirect('facturacion:cobros')
        except ValueError:
            messages.error(request, 'La fecha no es válida.')
        except ErrorNegocio as error:
            messages.error(request, error.message)
    deudores = [c for c in Cliente.objects.filter(comprobantes__forma_pago='credito',
                                                    comprobantes__estado='emitido').distinct() if c.saldo() > 0]
    return render(request, 'facturacion/cobro_form.html', {'clientes': deudores, 'hoy': timezone.localdate(),
                                                           'cliente_inicial': request.POST.get('cliente_id') or request.GET.get('cliente', ''),
                                                           'formas': Cobro.FORMAS})


@acceso()
def pendientes(request):
    cliente = Cliente.objects.filter(pk=servicios._id(request.GET.get('cliente'))).first()
    if cliente is None:
        return JsonResponse({'facturas': []})
    return JsonResponse({'facturas': [{'id': f.pk, 'numero': f.numero, 'fecha': f'{f.fecha:%d/%m/%Y}',
                                       'vence': f'{f.vencimiento:%d/%m/%Y}' if f.vencimiento else '',
                                       'total': f'{f.total:.2f}', 'saldo': f'{f.saldo():.2f}'}
                                      for f in servicios.pendientes(cliente)]})


@require_POST
@acceso(solo_admin=True)
def cobro_anular(request, pk):
    try:
        servicios.anular_cobro(get_object_or_404(Cobro, pk=pk), request.user, request.POST.get('motivo', ''))
        messages.success(request, 'Cobro anulado: las facturas recuperan su saldo.')
    except ErrorNegocio as error:
        messages.error(request, error.message)
    return redirect('facturacion:cobros')


# ---------------------------------------------------------------------
#  Reportes
# ---------------------------------------------------------------------
@acceso()
def reporte_ventas(request):
    desde, hasta = rango_mes(request)
    r = servicios.resumen_ventas(desde, hasta)
    dias = servicios.ventas_por_dia(desde, hasta)
    por_servicio = servicios.ventas_por_servicio(desde, hasta)
    if request.GET.get('formato') == 'excel':
        filas = [[c.fecha, c.get_tipo_display(), c.numero, c.cliente.identificacion, c.cliente.nombre,
                  c.decreto.feriado if c.decreto else '', c.base_general, c.base_reducida, c.base_cero, c.iva, c.total,
                  c.get_estado_display()]
                 for c in Comprobante.objects.filter(fecha__gte=desde, fecha__lte=hasta).exclude(estado='borrador')
                 .select_related('cliente', 'decreto').order_by('fecha', 'id')]
        return excel('ventas', f'Ventas del {desde:%d/%m/%Y} al {hasta:%d/%m/%Y}',
                     ['Fecha', 'Documento', 'Número', 'Identificación', 'Cliente', 'Feriado', 'Base tarifa general',
                      'Base IVA reducido', 'Base 0 %', 'IVA', 'Total', 'Estado'], filas, columnas_dinero=(7, 8, 9, 10, 11))
    return render(request, 'facturacion/reporte_ventas.html', {
        'r': r, 'dias': dias, 'por_servicio': por_servicio, 'desde': desde, 'hasta': hasta,
        'grafico': graficos.barras_diarias(desde, hasta, dias) if (hasta - desde).days <= 62 else None})


@acceso()
def reporte_iva(request):
    hoy = timezone.localdate()
    try:
        anio, mes = int(request.GET.get('anio', hoy.year)), int(request.GET.get('mes', hoy.month))
        desde = date(anio, mes, 1)
    except ValueError:
        anio, mes, desde = hoy.year, hoy.month, hoy.replace(day=1)
    hasta = (desde.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    r = servicios.resumen_ventas(desde, hasta)
    return render(request, 'facturacion/reporte_iva.html', {'r': r, 'anio': anio, 'mes': mes, 'mes_nombre': MESES[mes - 1],
                                                            'meses': list(enumerate(MESES, 1)),
                                                            'anios': range(hoy.year - 3, hoy.year + 1)})


@acceso()
def reporte_cartera(request):
    corte = fecha_param(request, 'corte', timezone.localdate())
    return render(request, 'facturacion/reporte_cartera.html', {'c': servicios.cartera(corte), 'corte': corte})


# ---------------------------------------------------------------------
#  Configuración (administrador)
# ---------------------------------------------------------------------
@acceso(solo_admin=True)
def empresa(request):
    obj = Empresa.actual()
    form = EmpresaForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        servicios.registrar(request.user, 'Actualizó datos de la empresa', form.instance.ruc)
        messages.success(request, 'Datos del negocio guardados.')
        return redirect('facturacion:inicio')
    return render(request, 'facturacion/form_simple.html', {
        'form': form, 'titulo': 'Datos del negocio',
        'ayuda': 'Estos datos salen en cada factura electrónica. Deben coincidir con tu RUC en el SRI.',
        'volver': reverse('facturacion:inicio')})


@acceso(solo_admin=True)
def usuarios(request):
    if request.method == 'POST':
        emp = get_object_or_404(Empleado, pk=request.POST.get('id'))
        form = EmpleadoForm(request.POST, instance=emp)
        if emp.usuario_id == request.user.id:
            messages.error(request, 'No puedes cambiar tu propio rol ni deshabilitarte.')
        elif form.is_valid():
            form.save()
            servicios.registrar(request.user, 'Cambió permisos', f'{emp.usuario.nombres}: {emp.get_rol_display()}, '
                                                                 f'{"habilitado" if emp.activo else "deshabilitado"}')
            messages.success(request, f'{emp.usuario.nombres} actualizado.')
        return redirect('facturacion:usuarios')
    from cuentas.models import Usuario
    for u in Usuario.objects.filter(empleado__isnull=True):
        empleado_de(u)
    return render(request, 'facturacion/usuarios.html', {'empleados': Empleado.objects.select_related('usuario')
                                                         .order_by('activo', 'usuario__nombres'),
                                                         'roles': Empleado.ROLES})


@acceso(solo_admin=True)
def bitacora(request):
    qs = Actividad.objects.select_related('usuario')
    if request.GET.get('ia') == '1':
        qs = qs.filter(por_ia=True)
    return render(request, 'facturacion/bitacora.html', {'actividades': qs[:300], 'solo_ia': request.GET.get('ia') == '1'})
