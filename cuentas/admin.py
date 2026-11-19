from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm

from .models import RegistroAcceso, Usuario


class UsuarioCreacionForm(BaseUserCreationForm):
    class Meta:
        model = Usuario
        fields = ('cedula', 'nombres', 'correo')


class UsuarioCambioForm(UserChangeForm):
    class Meta:
        model = Usuario
        fields = '__all__'


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    add_form = UsuarioCreacionForm
    form = UsuarioCambioForm
    ordering = ['nombres']
    list_display = ['cedula', 'nombres', 'correo', 'correo_verificado', 'intentos_fallidos',
                    'bloqueado_hasta', 'is_active', 'last_login']
    list_filter = ['correo_verificado', 'is_active', 'is_staff']
    search_fields = ['cedula', 'nombres', 'correo']
    readonly_fields = ['last_login', 'fecha_registro', 'token_verificacion', 'expira_verificacion']
    actions = ['desbloquear']

    fieldsets = [
        ('Datos', {'fields': ['cedula', 'nombres', 'correo', 'password']}),
        ('Seguridad', {'fields': ['correo_verificado', 'intentos_fallidos', 'bloqueado_hasta',
                                  'token_verificacion', 'expira_verificacion']}),
        ('Permisos', {'fields': ['is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions']}),
        ('Fechas', {'fields': ['last_login', 'fecha_registro']}),
    ]
    add_fieldsets = [(None, {'classes': ['wide'],
                             'fields': ['cedula', 'nombres', 'correo', 'password1', 'password2']})]

    @admin.action(description='Desbloquear cuentas seleccionadas')
    def desbloquear(self, request, queryset):
        n = queryset.update(intentos_fallidos=0, bloqueado_hasta=None)
        self.message_user(request, f'{n} cuenta(s) desbloqueada(s).')


@admin.register(RegistroAcceso)
class RegistroAccesoAdmin(admin.ModelAdmin):
    list_display = ['fecha', 'accion', 'estado', 'usuario', 'ip', 'detalle']
    list_filter = ['accion', 'estado']
    search_fields = ['usuario', 'ip', 'detalle']
    date_hierarchy = 'fecha'

    # La bitácora no se puede modificar ni borrar desde el administrador
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
