from django.urls import path

from . import views

urlpatterns = [
    path('', views.login_vista, name='login'),
    path('login/validar/', views.validar_login, name='validar_login'),
    path('registro/', views.registro_vista, name='registro'),
    path('registro/procesar/', views.procesar_registro, name='procesar_registro'),
    path('verificar-correo/', views.reenviar_verificacion, name='reenviar_verificacion'),
    path('verificar-correo/codigo/', views.verificar_codigo, name='verificar_codigo'),
    path('verificar-correo/reenviar/', views.reenviar_codigo, name='reenviar_codigo'),
    path('panel/', views.panel, name='panel'),
    path('salir/', views.salir, name='salir'),
]
