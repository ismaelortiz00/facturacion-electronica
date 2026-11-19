"""
FACTURACIÓN ELECTRÓNICA PARA NEGOCIOS TURÍSTICOS
Empresa emisora, empleados, clientes, servicios, tarifas de IVA con vigencia,
decretos de feriado (IVA reducido para turismo), facturas, notas de crédito y cobros.

Regla central: el IVA de cada línea se decide en el servidor según la fecha de la
factura, el tipo de servicio y los decretos registrados. Nadie lo cambia a mano.
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Sum

CERO = Decimal('0.00')


class ErrorNegocio(ValidationError):
    """Error de una regla del negocio; su mensaje se muestra tal cual al usuario."""


# ---------------------------------------------------------------------
#  Empresa emisora (una por instalación) y sus empleados
# ---------------------------------------------------------------------
class Empresa(models.Model):
    REGIMENES = [('general', 'Régimen general'), ('rimpe_emprendedor', 'RIMPE emprendedor'),
                 ('rimpe_popular', 'RIMPE negocio popular')]
    ruc = models.CharField('RUC', max_length=13, unique=True)
    razon_social = models.CharField('razón social', max_length=200)
    nombre_comercial = models.CharField('nombre comercial', max_length=200, blank=True)
    direccion = models.CharField('dirección matriz', max_length=250)
    telefono = models.CharField('teléfono', max_length=30, blank=True)
    correo = models.EmailField('correo', blank=True)
    obligado_contabilidad = models.BooleanField('obligado a llevar contabilidad', default=False)
    regimen = models.CharField('régimen tributario', max_length=20, choices=REGIMENES, default='general')
    contribuyente_especial = models.CharField('n.º resolución contribuyente especial', max_length=20, blank=True)
    agente_retencion = models.CharField('n.º resolución agente de retención', max_length=20, blank=True)
    establecimiento = models.CharField('código de establecimiento', max_length=3, default='001')
    punto_emision = models.CharField('punto de emisión', max_length=3, default='001')
    actividad = models.CharField('actividad turística', max_length=120, blank=True,
                                 help_text='Ej. hotel, agencia de viajes, restaurante, operadora de turismo')

    class Meta:
        verbose_name = 'empresa'

    def __str__(self):
        return self.nombre_comercial or self.razon_social

    @classmethod
    def actual(cls):
        return cls.objects.order_by('id').first()


class Empleado(models.Model):
    ROLES = [('admin', 'Administrador'), ('vendedor', 'Vendedor / cajero')]
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='empleado')
    rol = models.CharField(max_length=10, choices=ROLES, default='vendedor')
    activo = models.BooleanField('habilitado', default=False)
    creado = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.usuario.nombres} ({self.get_rol_display()})'

    @property
    def es_admin(self):
        return self.rol == 'admin' and self.activo


# ---------------------------------------------------------------------
#  Clientes
# ---------------------------------------------------------------------
class Cliente(models.Model):
    TIPOS_ID = [('cedula', 'Cédula'), ('ruc', 'RUC'), ('pasaporte', 'Pasaporte (turista extranjero)'),
                ('consumidor', 'Consumidor final')]
    tipo_identificacion = models.CharField('tipo de identificación', max_length=10, choices=TIPOS_ID)
    identificacion = models.CharField('identificación', max_length=20, unique=True)
    nombre = models.CharField('nombre o razón social', max_length=250)
    correo = models.EmailField(blank=True, help_text='Aquí llegan el PDF y el XML de sus facturas.')
    telefono = models.CharField('teléfono', max_length=30, blank=True)
    direccion = models.CharField('dirección', max_length=250, blank=True)
    pais = models.CharField('país', max_length=60, blank=True, default='Ecuador')
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return f'{self.nombre} ({self.identificacion})'

    def saldo(self):
        return sum((f.saldo() for f in self.comprobantes.filter(tipo='factura', estado='emitido',
                                                                  forma_pago='credito')), CERO)


# ---------------------------------------------------------------------
#  Servicios (y productos) que se facturan
# ---------------------------------------------------------------------
class Servicio(models.Model):
    CATEGORIAS = [('alojamiento', 'Alojamiento'), ('alimentos', 'Alimentos y bebidas'), ('tour', 'Tour o excursión'),
                  ('transporte', 'Transporte turístico'), ('agencia', 'Servicio de agencia de viajes'),
                  ('recreacion', 'Recreación y entretenimiento'), ('otro', 'Otro')]
    TARIFAS = [('general', 'Tarifa general vigente'), ('0', '0 %'), ('no_objeto', 'No objeto de IVA'),
               ('exento', 'Exento de IVA')]
    codigo = models.CharField('código', max_length=25, unique=True)
    nombre = models.CharField(max_length=200)
    categoria = models.CharField('categoría', max_length=12, choices=CATEGORIAS, default='otro')
    precio = models.DecimalField('precio sin IVA', max_digits=12, decimal_places=4)
    tarifa_iva = models.CharField('IVA', max_length=10, choices=TARIFAS, default='general')
    turistico = models.BooleanField('es servicio turístico', default=True,
                                    help_text='Los servicios turísticos pagan el IVA reducido en los feriados con decreto.')
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return f'{self.codigo} · {self.nombre}'


# ---------------------------------------------------------------------
#  IVA: tarifa general con vigencia y decretos de feriado
# ---------------------------------------------------------------------
class TasaIva(models.Model):
    porcentaje = models.DecimalField(max_digits=5, decimal_places=2)
    desde = models.DateField('vigente desde', unique=True)
    base_legal = models.CharField('base legal', max_length=250, blank=True)

    class Meta:
        ordering = ['-desde']
        verbose_name = 'tarifa general de IVA'

    def __str__(self):
        return f'{self.porcentaje.normalize():f} % desde {self.desde:%d/%m/%Y}'


class DecretoFeriado(models.Model):
    """Decreto ejecutivo que reduce el IVA de los servicios turísticos durante un feriado."""
    feriado = models.CharField(max_length=120, help_text='Ej. Carnaval, Semana Santa, Difuntos')
    decreto = models.CharField('n.º de decreto', max_length=120)
    desde = models.DateField()
    hasta = models.DateField()
    porcentaje = models.DecimalField('IVA reducido (%)', max_digits=5, decimal_places=2, default=Decimal('8'))
    registro_oficial = models.CharField('registro oficial / enlace', max_length=250, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-desde']
        verbose_name = 'decreto de feriado'
        constraints = [models.CheckConstraint(condition=Q(hasta__gte=models.F('desde')), name='decreto_fechas_validas')]

    def __str__(self):
        return f'{self.feriado} ({self.desde:%d/%m} al {self.hasta:%d/%m/%Y}) · IVA {self.porcentaje.normalize():f} %'


# ---------------------------------------------------------------------
#  Facturas y notas de crédito
# ---------------------------------------------------------------------
class Secuencia(models.Model):
    tipo = models.CharField(max_length=15)
    establecimiento = models.CharField(max_length=3)
    punto = models.CharField(max_length=3)
    ultimo = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['tipo', 'establecimiento', 'punto'], name='secuencia_unica')]


class Comprobante(models.Model):
    TIPOS = [('factura', 'Factura'), ('nota_credito', 'Nota de crédito')]
    ESTADOS = [('borrador', 'Borrador'), ('emitido', 'Emitida'), ('anulado', 'Anulada')]
    FORMAS_PAGO = [('efectivo', 'Efectivo'), ('transferencia', 'Transferencia'), ('tarjeta_credito', 'Tarjeta de crédito'),
                   ('tarjeta_debito', 'Tarjeta de débito'), ('credito', 'A crédito (paga después)')]
    tipo = models.CharField(max_length=12, choices=TIPOS, default='factura')
    establecimiento = models.CharField(max_length=3, default='001')
    punto_emision = models.CharField(max_length=3, default='001')
    secuencial = models.PositiveIntegerField(null=True, blank=True)
    fecha = models.DateField()
    cliente = models.ForeignKey(Cliente, models.PROTECT, related_name='comprobantes')
    modifica = models.ForeignKey('self', models.PROTECT, null=True, blank=True, related_name='notas',
                                 verbose_name='factura que modifica')
    motivo = models.CharField(max_length=300, blank=True)
    forma_pago = models.CharField('forma de pago', max_length=16, choices=FORMAS_PAGO, default='efectivo')
    plazo_dias = models.PositiveIntegerField('plazo (días)', default=0)
    vencimiento = models.DateField(null=True, blank=True)
    observacion = models.CharField('observación', max_length=300, blank=True)

    base_general = models.DecimalField('base tarifa general', max_digits=14, decimal_places=2, default=CERO)
    base_reducida = models.DecimalField('base IVA reducido (feriado)', max_digits=14, decimal_places=2, default=CERO)
    base_cero = models.DecimalField('base 0 %, exenta o no objeto', max_digits=14, decimal_places=2, default=CERO)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=CERO)
    descuento = models.DecimalField(max_digits=14, decimal_places=2, default=CERO)
    iva = models.DecimalField(max_digits=14, decimal_places=2, default=CERO)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=CERO)
    decreto = models.ForeignKey(DecretoFeriado, models.PROTECT, null=True, blank=True, related_name='comprobantes',
                                verbose_name='decreto aplicado')

    estado = models.CharField(max_length=8, choices=ESTADOS, default='borrador')
    motivo_anulacion = models.CharField(max_length=300, blank=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, models.PROTECT, related_name='+')
    creado_en = models.DateTimeField(auto_now_add=True)
    origen = models.CharField(max_length=10, default='manual', choices=[('manual', 'Pantalla'), ('ia', 'Asistente IA')])

    class Meta:
        ordering = ['-fecha', '-id']
        constraints = [models.UniqueConstraint(fields=['tipo', 'establecimiento', 'punto_emision', 'secuencial'],
                                               name='comprobante_numero_unico', condition=Q(secuencial__isnull=False))]

    def __str__(self):
        return f'{self.get_tipo_display()} {self.numero or "(borrador)"}'

    @property
    def numero(self):
        return f'{self.establecimiento}-{self.punto_emision}-{self.secuencial:09d}' if self.secuencial else ''

    @property
    def empresa(self):
        return Empresa.actual()

    def saldo(self):
        """Lo que el cliente todavía debe de esta factura."""
        if self.tipo != 'factura' or self.estado != 'emitido':
            return CERO
        if self.forma_pago != 'credito':
            return CERO
        cobrado = self.aplicaciones.filter(cobro__estado='emitido').aggregate(s=Sum('valor'))['s'] or CERO
        notas = self.notas.filter(estado='emitido').aggregate(s=Sum('total'))['s'] or CERO
        # Si una nota de crédito llega después de un pago, la diferencia se devuelve al cliente: el saldo no queda negativo
        return max(self.total - cobrado - notas, CERO)


class Linea(models.Model):
    comprobante = models.ForeignKey(Comprobante, on_delete=models.CASCADE, related_name='lineas')
    servicio = models.ForeignKey(Servicio, models.PROTECT, related_name='+')
    descripcion = models.CharField(max_length=300)
    cantidad = models.DecimalField(max_digits=12, decimal_places=4)
    precio = models.DecimalField(max_digits=12, decimal_places=4)
    descuento = models.DecimalField(max_digits=12, decimal_places=2, default=CERO)
    tarifa = models.CharField(max_length=10)
    porcentaje_iva = models.DecimalField(max_digits=5, decimal_places=2)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2)
    iva = models.DecimalField(max_digits=14, decimal_places=2)
    decreto = models.ForeignKey(DecretoFeriado, models.PROTECT, null=True, blank=True, related_name='+')

    class Meta:
        ordering = ['id']

    @property
    def motivo_iva(self):
        if self.decreto_id:
            return f'IVA {self.porcentaje_iva.normalize():f} % por {self.decreto.feriado} ({self.decreto.decreto})'
        if self.tarifa != 'general':
            return dict(Servicio.TARIFAS).get(self.tarifa, '')
        return f'Tarifa general {self.porcentaje_iva.normalize():f} %'


# ---------------------------------------------------------------------
#  Cobros
# ---------------------------------------------------------------------
class Cobro(models.Model):
    FORMAS = [('efectivo', 'Efectivo'), ('transferencia', 'Transferencia'), ('tarjeta', 'Tarjeta'), ('cheque', 'Cheque')]
    numero = models.PositiveIntegerField(unique=True)
    fecha = models.DateField()
    cliente = models.ForeignKey(Cliente, models.PROTECT, related_name='cobros')
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    forma = models.CharField('forma de cobro', max_length=13, choices=FORMAS, default='efectivo')
    referencia = models.CharField('referencia', max_length=80, blank=True)
    estado = models.CharField(max_length=8, choices=[('emitido', 'Vigente'), ('anulado', 'Anulado')], default='emitido')
    motivo_anulacion = models.CharField(max_length=300, blank=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, models.PROTECT, related_name='+')
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha', '-id']

    def __str__(self):
        return f'Cobro n.º {self.numero}'


class Aplicacion(models.Model):
    cobro = models.ForeignKey(Cobro, on_delete=models.CASCADE, related_name='aplicaciones')
    factura = models.ForeignKey(Comprobante, models.PROTECT, related_name='aplicaciones')
    valor = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(valor__gt=0), name='aplicacion_valor_positivo')]


# ---------------------------------------------------------------------
#  Bitácora de actividades (quién hizo qué: personas y asistente de IA)
# ---------------------------------------------------------------------
class Actividad(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, models.SET_NULL, null=True, related_name='+')
    accion = models.CharField(max_length=60)
    detalle = models.CharField(max_length=400, blank=True)
    por_ia = models.BooleanField(default=False)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha']
