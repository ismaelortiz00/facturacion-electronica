"""Diagnóstico de la conexión con la IA:  python manage.py probar_ia
Muestra qué motor está activo y, si falla, el mensaje exacto del proveedor (nunca imprime la clave completa)."""
import json
import urllib.error

from django.conf import settings
from django.core.management.base import BaseCommand

from asistente import ia


class Command(BaseCommand):
    help = 'Prueba la conexión con Gemini o Claude y muestra el error exacto si falla.'

    def handle(self, *args, **opciones):
        motor, nombre = ia.motor()
        self.stdout.write(f'Motor configurado: {nombre}')
        if settings.GROQ_API_KEY:
            clave = settings.GROQ_API_KEY
            self.stdout.write(f'Clave de Groq: empieza con "{clave[:4]}…", tiene {len(clave)} caracteres')
            modelos = [settings.GROQ_MODEL] if settings.GROQ_MODEL else ia.GROQ_MODELOS
            for modelo in modelos:
                try:
                    r = ia._llamar_groq(modelo, {'messages': [{'role': 'user', 'content': 'Responde solo: OK'}], 'max_tokens': 50})
                    texto = (r['choices'][0]['message'].get('content') or '').strip()
                    self.stdout.write(self.style.SUCCESS(f'Groq funciona con {modelo}. Respuesta: {texto[:40]}'))
                    break
                except ia.ErrorGroq as error:
                    self.stdout.write(self.style.ERROR(f'Groq {modelo}: error {error.codigo} → {error.mensaje}'))
                    if error.codigo not in (400, 404):
                        break
                except urllib.error.URLError as error:
                    self.stdout.write(self.style.ERROR(f'Sin conexión: {error.reason}'))
                    break
            if not settings.GEMINI_API_KEY or motor == 'groq':
                return
        if motor == 'basico':
            self.stdout.write(self.style.WARNING('No hay GEMINI_API_KEY ni ANTHROPIC_API_KEY en el .env (o el servidor no lo leyó).'))
            return
        if settings.GEMINI_API_KEY and motor in ('gemini', 'groq'):
            clave = settings.GEMINI_API_KEY
            self.stdout.write(f'Clave leída: empieza con "{clave[:4]}…", tiene {len(clave)} caracteres')
            if clave != clave.strip() or clave[:1] in '"\'':
                self.stdout.write(self.style.WARNING('La clave tiene espacios o comillas: quítalos en el .env.'))
            if clave.startswith('AIza'):
                self.stdout.write(self.style.WARNING('Es una clave ANTIGUA (AIza…). Google ya no las acepta sin restricciones: '
                                                     'crea una nueva en aistudio.google.com.'))
            modelos = [settings.GEMINI_MODEL] if settings.GEMINI_MODEL else ia.GEMINI_MODELOS
            for modelo in modelos:
                try:
                    r = ia._llamar_gemini(modelo, {'contents': [{'role': 'user', 'parts': [{'text': 'Responde solo: OK'}]}]})
                    texto = r['candidates'][0]['content']['parts'][0].get('text', '').strip()
                    self.stdout.write(self.style.SUCCESS(f'Conexión correcta con {modelo}. Respuesta: {texto}'))
                    if modelo != modelos[0]:
                        self.stdout.write(f'Sugerencia: agrega GEMINI_MODEL={modelo} al .env')
                    return
                except ia.ErrorGemini as error:
                    self.stdout.write(self.style.ERROR(f'{modelo}: error {error.codigo} → {error.mensaje}'))
                    if error.codigo != 404:
                        return
                except urllib.error.URLError as error:
                    self.stdout.write(self.style.ERROR(f'Sin conexión: {error.reason}'))
                    return
        else:
            try:
                import anthropic
                c = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=30)
                m = c.messages.create(model=settings.ANTHROPIC_MODEL, max_tokens=10, messages=[{'role': 'user', 'content': 'Responde solo: OK'}])
                self.stdout.write(self.style.SUCCESS(f'Conexión correcta con Claude: {m.content[0].text}'))
            except Exception as error:
                self.stdout.write(self.style.ERROR(f'Claude: {error}'))
