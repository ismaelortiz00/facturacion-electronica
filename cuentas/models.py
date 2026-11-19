"""
Modelos: usuario que inicia sesión con su cédula, bitácora e intentos por IP.
La contraseña la encripta Django con Argon2 (campo `password`).
"""
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from .validadores import validar_cedula


class UsuarioManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, cedula, correo, nombres, password=None, **extra):
        if not correo:
            raise ValueError('El correo es obligatorio.')
        usuario = self.model(cedula=cedula, correo=self.normalize_email(correo).lower(), nombres=nombres, **extra)
        usuario.full_clean(exclude=['password'])
        usuario.set_password(password)  # encripta con Argon2
        usuario.save(using=self._db)
        return usuario

    def create_superuser(self, cedula, correo, nombres, password=None, **extra):
        extra.update(is_staff=True, is_superuser=True, correo_verificado=True)
        return self.create_user(cedula, correo, nombres, password, **extra)


class Usuario(AbstractBaseUser, PermissionsMixin):
    cedula = models.CharField('cédula', max_length=10, unique=True, validators=[validar_cedula])
    nombres = models.CharField('nombres y apellidos', max_length=120)
    correo = models.EmailField('correo electrónico', max_length=150, unique=True)

    correo_verificado = models.BooleanField(default=False)
    token_verificacion = models.CharField(max_length=64, null=True, blank=True)  # hash del código de 6 dígitos
    expira_verificacion = models.DateTimeField(null=True, blank=True)
    intentos_codigo = models.PositiveSmallIntegerField(default=0)

    intentos_fallidos = models.PositiveSmallIntegerField(default=0)
    bloqueado_hasta = models.DateTimeField(null=True, blank=True)

    is_active = models.BooleanField('activo', default=True)
    is_staff = models.BooleanField('acceso al administrador', default=False)
    fecha_registro = models.DateTimeField(default=timezone.now)

    objects = UsuarioManager()

    USERNAME_FIELD = 'cedula'
    EMAIL_FIELD = 'correo'
    REQUIRED_FIELDS = ['correo', 'nombres']

    class Meta:
        verbose_name = 'usuario'
        verbose_name_plural = 'usuarios'

    def __str__(self):
        return f'{self.nombres} ({self.cedula})'

    @property
    def segundos_bloqueo(self) -> int:
        if self.bloqueado_hasta and self.bloqueado_hasta > timezone.now():
            return int((self.bloqueado_hasta - timezone.now()).total_seconds()) + 1
        return 0

    @property
    def cedula_oculta(self) -> str:
        return self.cedula[:2] + '*' * 6 + self.cedula[-2:]


class RegistroAcceso(models.Model):
    """Bitácora de auditoría: cada evento de seguridad queda registrado."""
    accion = models.CharField(max_length=40)
    usuario = models.CharField(max_length=150, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    estado = models.CharField(max_length=20)
    detalle = models.CharField(max_length=500, blank=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'registro de acceso'
        verbose_name_plural = 'bitácora de accesos'
        ordering = ['-fecha']
        indexes = [models.Index(fields=['accion', 'usuario', 'fecha'])]

    def __str__(self):
        return f'{self.fecha:%Y-%m-%d %H:%M} {self.accion} {self.estado}'


class IntentoIP(models.Model):
    """Fallos por IP: frena ataques que prueban muchos usuarios."""
    ip = models.GenericIPAddressField()
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['ip', 'fecha'])]
