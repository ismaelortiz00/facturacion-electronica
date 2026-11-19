from django.urls import path

from . import views

app_name = 'asistente'

urlpatterns = [
    path('', views.pagina, name='pagina'),
    path('historial/', views.historial, name='historial'),
    path('mensaje/', views.mensaje, name='mensaje'),
]
