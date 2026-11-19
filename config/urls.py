from django.contrib import admin
from django.urls import include, path

admin.site.site_header = 'Facturación Turística IK - Administración'
admin.site.site_title = 'Facturación Turística IK'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('app/', include('facturacion.urls')),
    path('sri/', include('sri.urls')),
    path('asistente/', include('asistente.urls')),
    path('', include('cuentas.urls')),
]
