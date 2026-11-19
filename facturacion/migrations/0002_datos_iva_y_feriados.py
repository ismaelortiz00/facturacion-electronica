"""
Datos iniciales: tarifa general de IVA y los decretos de 2026 que redujeron al 8 % el IVA de los
servicios turísticos en feriados (art. 5 de la Ley de Turismo). Los nuevos decretos se registran
desde la pantalla «Feriados e IVA».
"""
from datetime import date
from decimal import Decimal

from django.db import migrations

TASAS = [
    (Decimal('12'), date(2001, 1, 1), 'Tarifa general 12 %'),
    (Decimal('14'), date(2016, 6, 1), 'Ley de Solidaridad: 14 % temporal'),
    (Decimal('12'), date(2017, 6, 1), 'Regreso al 12 %'),
    (Decimal('15'), date(2024, 4, 1), 'Decreto Ejecutivo 198: 15 % desde el 1 de abril de 2024'),
]

DECRETOS_2026 = [
    ('Año Nuevo', 'Decreto Ejecutivo 271', date(2026, 1, 1), date(2026, 1, 4)),
    ('Carnaval', 'Decreto Ejecutivo 304', date(2026, 2, 14), date(2026, 2, 17)),
    ('Semana Santa', 'Decreto Ejecutivo 348', date(2026, 4, 3), date(2026, 4, 5)),
    ('Día del Trabajo', 'Decreto Ejecutivo 368', date(2026, 4, 30), date(2026, 5, 3)),
    ('Batalla de Pichincha', 'Decreto Ejecutivo 391', date(2026, 5, 23), date(2026, 5, 25)),
    ('Primer Grito de Independencia', 'Decreto Ejecutivo 465', date(2026, 8, 8), date(2026, 8, 10)),
    ('Independencia de Guayaquil', 'Decreto Ejecutivo 513', date(2026, 10, 9), date(2026, 10, 11)),
    ('Difuntos e Independencia de Cuenca', 'Decreto Ejecutivo 526', date(2026, 10, 31), date(2026, 11, 3)),
]


def cargar(apps, schema_editor):
    TasaIva = apps.get_model('facturacion', 'TasaIva')
    Decreto = apps.get_model('facturacion', 'DecretoFeriado')
    for pct, desde, base in TASAS:
        TasaIva.objects.get_or_create(desde=desde, defaults={'porcentaje': pct, 'base_legal': base})
    for feriado, decreto, desde, hasta in DECRETOS_2026:
        Decreto.objects.get_or_create(decreto=decreto, defaults={'feriado': feriado, 'desde': desde, 'hasta': hasta,
                                                                 'porcentaje': Decimal('8')})


class Migration(migrations.Migration):
    dependencies = [('facturacion', '0001_initial')]
    operations = [migrations.RunPython(cargar, migrations.RunPython.noop)]
