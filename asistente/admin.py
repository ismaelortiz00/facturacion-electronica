from django.contrib import admin

from .models import ConsultaIA


@admin.register(ConsultaIA)
class ConsultaIAAdmin(admin.ModelAdmin):
    list_display = ['fecha', 'usuario', 'modo', 'herramientas', 'pregunta']
    list_filter = ['modo']
    readonly_fields = [f.name for f in ConsultaIA._meta.fields]
