from django.conf import settings
from django.db import models


class ConsultaIA(models.Model):
    """Registro de cada conversación con el asistente (auditoría y costo)."""
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, models.SET_NULL, null=True, related_name='+')
    pregunta = models.TextField()
    respuesta = models.TextField()
    herramientas = models.CharField(max_length=300, blank=True)
    modo = models.CharField(max_length=10, choices=[('claude', 'Claude'), ('gemini', 'Gemini'), ('groq', 'Groq'), ('basico', 'Modo básico')])
    tokens_entrada = models.PositiveIntegerField(default=0)
    tokens_salida = models.PositiveIntegerField(default=0)
    duracion_ms = models.PositiveIntegerField(default=0)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha']
        verbose_name = 'consulta al asistente'
