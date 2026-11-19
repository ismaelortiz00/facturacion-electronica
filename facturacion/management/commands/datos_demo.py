"""
Carga un negocio turístico de ejemplo para practicar o presentar el sistema:
    python manage.py datos_demo
Crea la empresa (si no existe), servicios, clientes y facturas de los últimos meses,
incluidas ventas en feriados con IVA reducido, ventas a crédito, cobros y una nota de crédito.
Usa como responsable al primer administrador (crea antes uno con createsuperuser o registrándote).
"""
import random
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from cuentas.models import Usuario
from facturacion import servicios as S
from facturacion.models import Cliente, Comprobante, Empleado, Empresa, Servicio

SERVICIOS = [
    ('HAB-DOB', 'Habitación doble por noche', 'alojamiento', '60.00', True),
    ('HAB-SUI', 'Suite familiar por noche', 'alojamiento', '95.00', True),
    ('TOUR-MIN', 'Tour Mindo full day', 'tour', '120.00', True),
    ('TOUR-CAS', 'Tour cascadas y tarabita', 'tour', '45.00', True),
    ('ALM-TIP', 'Almuerzo típico', 'alimentos', '12.50', True),
    ('DES-BUF', 'Desayuno buffet', 'alimentos', '8.00', True),
    ('TRANS-QUI', 'Traslado Quito - Mindo', 'transporte', '35.00', True),
    ('SOUV-CAM', 'Camiseta recuerdo', 'otro', '15.00', False),
]


def cedula_valida(base9: str) -> str:
    total = 0
    for i, d in enumerate(base9):
        v = int(d) * (2 if i % 2 == 0 else 1)
        total += v - 9 if v > 9 else v
    return base9 + str((10 - total % 10) % 10)


class Command(BaseCommand):
    help = 'Carga datos de ejemplo de un negocio turístico.'

    def add_arguments(self, parser):
        parser.add_argument('--semilla', type=int, default=2026)

    @transaction.atomic
    def handle(self, *args, **opciones):
        rnd = random.Random(opciones['semilla'])
        admin = (Usuario.objects.filter(empleado__rol='admin', empleado__activo=True).first()
                 or Usuario.objects.filter(is_superuser=True).first())
        if admin is None:
            raise CommandError('Primero crea un administrador: python manage.py createsuperuser')
        Empleado.objects.get_or_create(usuario=admin, defaults={'rol': 'admin', 'activo': True})
        if Empresa.actual() is None:
            Empresa.objects.create(ruc='1792146739001', razon_social='Hostería Mindo Verde S.A.',
                                   nombre_comercial='Mindo Verde', actividad='Hostería y operadora de turismo',
                                   direccion='Vía a Mindo km 2, Pichincha', telefono='022 765 432',
                                   correo='reservas@mindoverde.ec', regimen='general', obligado_contabilidad=True)
        for codigo, nombre, cat, precio, turistico in SERVICIOS:
            Servicio.objects.get_or_create(codigo=codigo, defaults={'nombre': nombre, 'categoria': cat,
                                                                    'precio': Decimal(precio), 'turistico': turistico})
        clientes = [
            ('cedula', cedula_valida('171003406'), 'Luis Mora', 'luis.mora@correo.ec', 'Ecuador'),
            ('cedula', cedula_valida('091234567'), 'María José Andrade', 'mjandrade@correo.ec', 'Ecuador'),
            ('cedula', cedula_valida('010456789'), 'Carlos Pesántez', '', 'Ecuador'),
            ('ruc', '1790016919001', 'Agencia Viajes del Sol Cía. Ltda.', 'compras@viajesdelsol.ec', 'Ecuador'),
            ('pasaporte', 'X4587921', 'Emma Schneider', 'emma.s@example.com', 'Alemania'),
            ('pasaporte', 'P93827461', 'John Carter', 'jcarter@example.com', 'Estados Unidos'),
            ('consumidor', '9999999999999', 'Consumidor final', '', 'Ecuador'),
        ]
        objetos = []
        for tipo, ident, nombre, correo, pais in clientes:
            c, _ = Cliente.objects.get_or_create(identificacion=ident, defaults={
                'tipo_identificacion': tipo, 'nombre': nombre, 'correo': correo, 'pais': pais})
            objetos.append(c)
        if Comprobante.objects.exists():
            self.stdout.write('Ya hay facturas: no se crean más.')
            return
        servicios = list(Servicio.objects.all())
        hoy = timezone.localdate()
        dia, creadas = hoy - timedelta(days=75), 0
        while dia <= hoy:
            feriado = S.decreto_vigente(dia) is not None
            for _ in range(rnd.randint(2, 5) if feriado else rnd.randint(0, 2)):
                cliente = rnd.choice(objetos)
                lineas = [{'servicio_id': s.id, 'cantidad': rnd.choice([1, 1, 2, 3])}
                          for s in rnd.sample(servicios, rnd.randint(1, 3))]
                credito = cliente.tipo_identificacion == 'ruc' or (cliente.tipo_identificacion == 'cedula' and rnd.random() < .3)
                if cliente.tipo_identificacion == 'consumidor':
                    lineas = [{'servicio_id': Servicio.objects.get(codigo='ALM-TIP').id, 'cantidad': 2}]
                comp = S.guardar(admin, {'fecha': dia, 'cliente_id': cliente.id,
                                         'forma_pago': 'credito' if credito else rnd.choice(['efectivo', 'transferencia', 'tarjeta_credito']),
                                         'plazo_dias': 30}, lineas)
                S.emitir(comp, admin)
                creadas += 1
            dia += timedelta(days=1)
        # Cobros de algunas facturas a crédito y una nota de crédito
        for f in Comprobante.objects.filter(forma_pago='credito', fecha__lt=hoy - timedelta(days=20))[::2]:
            S.registrar_cobro(admin, {'fecha': min(f.fecha + timedelta(days=15), hoy), 'cliente_id': f.cliente_id,
                                      'forma': 'transferencia'}, [{'factura_id': f.id, 'valor': f.saldo()}])
        f = Comprobante.objects.filter(tipo='factura', forma_pago='credito').exclude(aplicaciones__isnull=False).first()
        if f:
            l = f.lineas.first()
            nc = S.guardar(admin, {'tipo': 'nota_credito', 'fecha': f.fecha + timedelta(days=1), 'cliente_id': f.cliente_id,
                                   'modifica_id': f.id, 'motivo': 'Cancelación de un servicio'},
                           [{'servicio_id': l.servicio_id, 'cantidad': 1, 'precio': l.precio}])
            S.emitir(nc, admin)
        self.stdout.write(self.style.SUCCESS(f'Listo: {creadas} facturas de ejemplo, con ventas en feriados al IVA reducido.'))
