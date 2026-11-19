from django.urls import path

from . import views

app_name = 'sri'

urlpatterns = [
    path('', views.comprobantes, name='comprobantes'),
    path('pendientes/', views.procesar_pendientes, name='pendientes'),
    path('firma/', views.firma, name='firma'),
    path('enviar/<int:pk>/', views.enviar, name='enviar'),
    path('<int:pk>/reintentar/', views.reintentar, name='reintentar'),
    path('<int:pk>/<str:formato>/', views.descargar, name='descargar'),
]
