"""
{% estatico 'css/app.css' %} → /static/css/app.css?v=3f9a1c2b
La versión es una huella del contenido del archivo: cuando el archivo cambia (por una
actualización), cambia la URL y el navegador descarga la versión nueva en lugar de usar
la copia guardada. Así nadie ve pantallas con estilos viejos.
"""
import hashlib

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()
_cache = {}


@register.simple_tag
def estatico(ruta):
    archivo = finders.find(ruta)
    if not archivo:
        return static(ruta)
    import os
    marca = os.path.getmtime(archivo)
    guardado = _cache.get(ruta)
    if guardado is None or guardado[0] != marca:
        with open(archivo, 'rb') as f:
            _cache[ruta] = (marca, hashlib.sha256(f.read()).hexdigest()[:10])
    return f'{static(ruta)}?v={_cache[ruta][1]}'
