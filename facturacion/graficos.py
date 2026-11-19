"""
Gráfico de ventas por día (SVG hecho en el servidor; la página no carga librerías externas).
Los días de feriado con decreto se pintan de otro color para ver el efecto del IVA reducido.
Colores validados para el fondo oscuro: normal #2fae6e, feriado #c98500.
"""
import math
from datetime import timedelta
from decimal import Decimal


def _n(v: float) -> str:
    return f'{v:.1f}'


def _tope(v: float) -> float:
    if v <= 0:
        return 1
    e = 10 ** math.floor(math.log10(v))
    return next(p * e for p in (1, 2, 2.5, 5, 10) if v <= p * e)


def _corto(v: float) -> str:
    return f'{v / 1000:.1f}k'.replace('.0k', 'k') if v >= 1000 else f'{v:.0f}'


def barras_diarias(desde, hasta, dias: list[dict]) -> dict:
    por_fecha = {d['fecha']: d for d in dias}
    fechas = [desde + timedelta(days=i) for i in range((hasta - desde).days + 1)]
    from .servicios import decreto_vigente
    ancho, alto, izq, der, arriba, abajo = 640, 210, 44, 8, 12, 26
    w, h = ancho - izq - der, alto - arriba - abajo
    maximo = _tope(float(max((max(d['total'], Decimal('0')) for d in dias), default=Decimal('0'))))
    paso = w / max(len(fechas), 1)
    barras = []
    for i, f in enumerate(fechas):
        d = por_fecha.get(f)
        total = float(d['total']) if d else 0.0
        alto_barra = max(h * max(total, 0) / maximo, 0)
        dec = decreto_vigente(f)
        barras.append({'x': _n(izq + i * paso + paso * 0.15), 'y': _n(arriba + h - alto_barra), 'ancho': _n(paso * 0.7),
                       'alto': _n(alto_barra), 'fecha': f, 'total': total, 'feriado': dec.feriado if dec else '',
                       'clase': 'serie-2' if dec else 'serie-1',
                       'etiqueta': f.day if (len(fechas) <= 16 or f.day % 5 == 0 or f.day == 1) else '',
                       'cx': _n(izq + i * paso + paso / 2)})
    grilla = [{'y': _n(arriba + h - h * k / 4), 'texto': _corto(maximo * k / 4)} for k in range(5)]
    return {'barras': barras, 'grilla': grilla, 'base': _n(arriba + h), 'izq': izq, 'der': ancho - der,
            'hay_feriado': any(b['feriado'] for b in barras), 'eje_y': _n(arriba + h + 17)}
