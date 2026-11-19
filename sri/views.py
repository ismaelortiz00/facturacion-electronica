"""Pantallas de comprobantes electrónicos y firma electrónica."""
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from facturacion.models import Comprobante, ErrorNegocio
from facturacion.views import acceso, volver_seguro

from . import ride, servicios
from .models import ComprobanteElectronico


@acceso()
def comprobantes(request):
    qs = ComprobanteElectronico.objects.select_related('comprobante', 'comprobante__cliente')
    estado = request.GET.get('estado', '')
    if estado:
        qs = qs.filter(estado=estado)
    pendientes = (ComprobanteElectronico.objects.exclude(estado__in=['autorizado', 'no_autorizado', 'devuelto']).count()
                  + Comprobante.objects.filter(estado='emitido', electronico__isnull=True).count())
    sin_enviar = (Comprobante.objects.filter(estado='emitido', electronico__isnull=True)
                  .select_related('cliente').order_by('-fecha', '-id'))
    return render(request, 'sri/comprobantes.html', {'comprobantes': qs[:300], 'estado': estado, 'pendientes': pendientes,
                                                     'sin_enviar': sin_enviar[:100], 'total_sin_enviar': sin_enviar.count(),
                                                     'estados': ComprobanteElectronico.ESTADOS, 'firma': servicios.firma_de()})


@require_POST
@acceso()
def procesar_pendientes(request):
    hechos = 0
    for c in Comprobante.objects.filter(estado='emitido', electronico__isnull=True)[:50]:
        ce, _ = servicios.emitir_electronico(c)
        hechos += 1 if ce else 0
    for ce in ComprobanteElectronico.objects.exclude(estado__in=['autorizado', 'no_autorizado', 'devuelto'])[:50]:
        servicios.procesar(ce)
        hechos += 1
    messages.success(request, f'{hechos} comprobante(s) procesados.')
    return redirect('sri:comprobantes')


@require_POST
@acceso()
def enviar(request, pk):
    comp = get_object_or_404(Comprobante, pk=pk)
    try:
        ce = servicios.procesar(servicios.generar_y_firmar(comp))
        (messages.success if ce.estado == 'autorizado' else messages.warning)(
            request, f'SRI: {ce.get_estado_display()}. {ce.mensajes[:300]}')
    except ErrorNegocio as error:
        messages.error(request, error.message)
    return redirect('facturacion:factura', pk=comp.pk)


@require_POST
@acceso()
def reintentar(request, pk):
    ce = servicios.procesar(get_object_or_404(ComprobanteElectronico, pk=pk))
    messages.info(request, f'SRI: {ce.get_estado_display()}.')
    return redirect(volver_seguro(request, request.POST.get('volver'), 'sri:comprobantes'))


@acceso()
def descargar(request, pk, formato):
    ce = get_object_or_404(ComprobanteElectronico, pk=pk)
    if formato == 'pdf':
        r = HttpResponse(ride.generar(ce), content_type='application/pdf')
        r['Content-Disposition'] = f'inline; filename="RIDE_{ce.clave_acceso}.pdf"'
    else:
        r = HttpResponse(ce.xml_autorizado or ce.xml_firmado, content_type='application/xml; charset=utf-8')
        r['Content-Disposition'] = f'attachment; filename="{ce.clave_acceso}.xml"'
    return r


@acceso(solo_admin=True)
def firma(request):
    actual = servicios.firma_de()
    if request.method == 'POST':
        archivo = request.FILES.get('archivo')
        try:
            if archivo:
                if archivo.size > 50_000:
                    raise ErrorNegocio('El archivo es demasiado grande para ser una firma .p12.')
                servicios.configurar_firma(None, archivo.read(), request.POST.get('clave', ''),
                                           request.POST.get('ambiente', '1'), bool(request.POST.get('enviar_correo')))
                messages.success(request, 'Firma electrónica guardada y verificada.')
            elif actual:
                actual.ambiente = request.POST.get('ambiente', actual.ambiente)
                actual.enviar_correo = bool(request.POST.get('enviar_correo'))
                actual.save()
                messages.success(request, 'Configuración guardada.')
            else:
                raise ErrorNegocio('Sube el archivo .p12 de la firma.')
            return redirect('sri:firma')
        except ErrorNegocio as error:
            messages.error(request, error.message)
    return render(request, 'sri/firma.html', {'firma': actual})
