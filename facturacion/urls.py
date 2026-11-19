from django.urls import path

from . import views

app_name = 'facturacion'

urlpatterns = [
    path('', views.inicio, name='inicio'),
    path('clientes/', views.clientes, name='clientes'),
    path('clientes/nuevo/', views.cliente_form, name='cliente_nuevo'),
    path('clientes/<int:pk>/', views.cliente_form, name='cliente_editar'),
    path('servicios/', views.servicios_lista, name='servicios'),
    path('servicios/nuevo/', views.servicio_form, name='servicio_nuevo'),
    path('servicios/<int:pk>/', views.servicio_form, name='servicio_editar'),
    path('feriados/', views.feriados, name='feriados'),
    path('facturas/', views.facturas, name='facturas'),
    path('facturas/nueva/', views.factura_form, name='factura_nueva'),
    path('facturas/calcular/', views.calcular, name='calcular'),
    path('facturas/<int:pk>/', views.factura, name='factura'),
    path('facturas/<int:pk>/editar/', views.factura_form, name='factura_editar'),
    path('facturas/<int:pk>/accion/', views.factura_accion, name='factura_accion'),
    path('cobros/', views.cobros, name='cobros'),
    path('cobros/nuevo/', views.cobro_form, name='cobro_nuevo'),
    path('cobros/pendientes/', views.pendientes, name='pendientes'),
    path('cobros/<int:pk>/anular/', views.cobro_anular, name='cobro_anular'),
    path('reportes/ventas/', views.reporte_ventas, name='reporte_ventas'),
    path('reportes/iva/', views.reporte_iva, name='reporte_iva'),
    path('reportes/cartera/', views.reporte_cartera, name='reporte_cartera'),
    path('configuracion/empresa/', views.empresa, name='empresa'),
    path('configuracion/usuarios/', views.usuarios, name='usuarios'),
    path('configuracion/bitacora/', views.bitacora, name='bitacora'),
]
