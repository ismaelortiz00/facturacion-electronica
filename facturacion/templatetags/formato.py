from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def dinero(valor):
    """1234.5 -> 1,234.50 ; vacío o cero -> ''  (para columnas de debe y haber)."""
    if valor in (None, ''):
        return ''
    valor = Decimal(valor)
    return f'{valor:,.2f}' if valor != 0 else ''


@register.filter
def dinero0(valor):
    """Igual que dinero pero muestra 0.00."""
    return f'{Decimal(valor or 0):,.2f}'


@register.filter
def sangria(nivel):
    return f'nivel-{min(int(nivel or 1), 5)}'


@register.simple_tag
def total(datos, clave):
    """Valor de un diccionario de totales, con formato de dinero."""
    return dinero0((datos or {}).get(clave, 0))


@register.simple_tag
def variacion(actual, anterior):
    """Variación porcentual frente al año anterior."""
    actual, anterior = Decimal(actual or 0), Decimal(anterior or 0)
    if anterior == 0:
        return '—' if actual == 0 else 'nuevo'
    return f'{(actual - anterior) / abs(anterior) * 100:+.1f} %'


@register.filter
def pct(valor):
    """15.00 -> 15 ; 8.50 -> 8.5"""
    if valor in (None, ''):
        return ''
    return f'{Decimal(valor).normalize():f}'


@register.simple_tag
def total_bases(r):
    """Suma de las tres bases del resumen de ventas (general + reducida + 0 %)."""
    n = r['neto']
    return dinero0(n['general'] + n['reducida'] + n['cero'])


@register.filter
def primer_nombre(nombres):
    """'Ana María Pérez' -> 'Ana'"""
    return (str(nombres or '').split() or [''])[0]


@register.filter
def precio(valor):
    """Precio unitario: mínimo 2 decimales, hasta 4 si hacen falta. 12.5000 -> 12.50 ; 0.1250 -> 0.125"""
    if valor in (None, ''):
        return ''
    texto = f'{Decimal(valor):,.4f}'.rstrip('0')
    entero, _, dec = texto.partition('.')
    return f'{entero}.{dec.ljust(2, "0")}'


@register.filter
def entrada(valor):
    """Número para un campo editable: sin separador de miles y sin ceros sobrantes (12.5000 -> 12.5).
    Si llega texto (lo que el usuario escribió y tuvo error), se devuelve tal cual para no perderlo."""
    if valor in (None, ''):
        return ''
    if isinstance(valor, str):
        return valor
    texto = f'{Decimal(valor):.4f}'.rstrip('0').rstrip('.')
    return texto or '0'
