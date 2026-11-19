from django.contrib import admin

from .models import Actividad, Cliente, Comprobante, DecretoFeriado, Empleado, Empresa, Servicio, TasaIva

admin.site.register([Empresa, Empleado, Cliente, Servicio, TasaIva, DecretoFeriado])


@admin.register(Comprobante)
class ComprobanteAdmin(admin.ModelAdmin):
    list_display = ['fecha', 'tipo', 'secuencial', 'cliente', 'total', 'estado']
    list_filter = ['tipo', 'estado']

    def has_change_permission(self, request, obj=None):
        return False  # las facturas solo se manejan desde el sistema (reglas del SRI)


@admin.register(Actividad)
class ActividadAdmin(admin.ModelAdmin):
    list_display = ['fecha', 'usuario', 'accion', 'detalle', 'por_ia']
    readonly_fields = [f.name for f in Actividad._meta.fields]
