"""Chat del asistente: panel flotante en todas las pantallas y página propia."""
import json

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from facturacion.views import acceso

from . import ia
from .models import ConsultaIA

CLAVE = 'asistente_historial'


@acceso()
def pagina(request):
    ultimas = ConsultaIA.objects.filter(usuario=request.user)[:20] if not request.empleado.es_admin else \
        ConsultaIA.objects.select_related('usuario')[:50]
    return render(request, 'asistente/pagina.html', {'consultas': ultimas, 'motor': ia.motor()[0],
                                                     'nombre_motor': ia.motor()[1]})


@acceso()
def historial(request):
    return JsonResponse({'mensajes': request.session.get(CLAVE, []), 'motor': ia.motor()[0]})


@require_POST
@acceso()
def mensaje(request):
    try:
        datos = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        datos = {}
    texto = (datos.get('texto') or '').strip()[:2000]
    if datos.get('limpiar'):
        request.session[CLAVE] = []
        return JsonResponse({'ok': True})
    if not texto:
        return JsonResponse({'error': 'Escribe tu pregunta.'}, status=400)
    previo = request.session.get(CLAVE, [])
    r = ia.responder(request.user, previo, texto, datos.get('pagina', ''))
    previo = (previo + [{'rol': 'user', 'texto': texto}, {'rol': 'assistant', 'texto': r['texto'],
                                                           'acciones': r['acciones']}])[-30:]
    request.session[CLAVE] = previo
    ConsultaIA.objects.create(usuario=request.user, pregunta=texto, respuesta=r['texto'][:5000],
                              herramientas=', '.join(r['herramientas'])[:300], modo=r['modo'],
                              tokens_entrada=r['tokens'][0], tokens_salida=r['tokens'][1], duracion_ms=r['duracion_ms'])
    return JsonResponse({'texto': r['texto'], 'acciones': r['acciones'], 'modo': r['modo'],
                         'herramientas': r['herramientas'], 'aviso': r.get('aviso', '')})
