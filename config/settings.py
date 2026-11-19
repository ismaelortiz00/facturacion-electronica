"""
Configuración de Facturación Turística IK (Django).

Los datos sensibles se leen de variables de entorno. En desarrollo hay
valores por defecto para que funcione con solo ejecutar `runserver`.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Lee las claves del archivo .env (junto a manage.py)
from dotenv import load_dotenv
load_dotenv(BASE_DIR / '.env')


def env(nombre, defecto=None):
    return os.environ.get(nombre, defecto)


def env_bool(nombre, defecto=False):
    return env(nombre, str(defecto)).lower() in ('1', 'true', 'si', 'sí', 'yes')


# ---------------------------------------------------------------------
#  Básico
# ---------------------------------------------------------------------
DEBUG = env_bool('DJANGO_DEBUG', True)
SECRET_KEY = env('DJANGO_SECRET_KEY', 'solo-para-desarrollo-cambiar-en-produccion')
if not DEBUG and SECRET_KEY.startswith('solo-para-desarrollo'):
    raise RuntimeError('Define DJANGO_SECRET_KEY en producción.')

ALLOWED_HOSTS = env('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
CSRF_TRUSTED_ORIGINS = [o for o in env('DJANGO_CSRF_ORIGINS', '').split(',') if o]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'cuentas',
    'facturacion',
    'sri',
    'asistente',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'cuentas.middleware.SesionSeguraMiddleware',
    'cuentas.middleware.CabecerasSeguridadMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'facturacion.contexto.datos_comunes',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# ---------------------------------------------------------------------
#  Base de datos: sqlite (desarrollo), postgresql o mssql (SQL Server)
# ---------------------------------------------------------------------
MOTOR_BD = env('DB_MOTOR', 'sqlite')

if MOTOR_BD == 'postgresql':
    DATABASES = {'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': env('DB_NOMBRE', 'facturacion_turistica'),
        'USER': env('DB_USUARIO', 'postgres'),
        'PASSWORD': env('DB_CLAVE', ''),
        'HOST': env('DB_SERVIDOR', 'localhost'),
        'PORT': env('DB_PUERTO', '5432'),
        'ATOMIC_REQUESTS': True,
    }}
elif MOTOR_BD == 'mssql':
    DATABASES = {'default': {
        'ENGINE': 'mssql',  # paquete mssql-django
        'NAME': env('DB_NOMBRE', 'facturacion_turistica'),
        'USER': env('DB_USUARIO', 'usuario_app'),
        'PASSWORD': env('DB_CLAVE', ''),
        'HOST': env('DB_SERVIDOR', 'localhost'),
        'PORT': env('DB_PUERTO', ''),
        'OPTIONS': {'driver': 'ODBC Driver 18 for SQL Server', 'extra_params': 'TrustServerCertificate=yes'},
        'ATOMIC_REQUESTS': True,
    }}
else:
    DATABASES = {'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
        'ATOMIC_REQUESTS': True,
    }}

# ---------------------------------------------------------------------
#  Usuarios y contraseñas
# ---------------------------------------------------------------------
AUTH_USER_MODEL = 'cuentas.Usuario'
LOGIN_URL = 'login'

# Argon2 es el algoritmo recomendado para encriptar contraseñas
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
]

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
     'OPTIONS': {'user_attributes': ('cedula', 'correo', 'nombres')}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'cuentas.validadores.ClaveSeguraValidator'},
]

# ---------------------------------------------------------------------
#  Reglas de seguridad del login
# ---------------------------------------------------------------------
MAX_INTENTOS = 3              # contraseñas erradas permitidas por usuario
MINUTOS_BLOQUEO = 15          # bloqueo al superar el límite
MAX_INTENTOS_IP = 10          # fallos permitidos por IP en MINUTOS_BLOQUEO
MINUTOS_SESION = 20           # cierre por inactividad
MINUTOS_CODIGO_CORREO = 10    # vigencia del código de verificación de 6 dígitos
MAX_INTENTOS_CODIGO = 5       # códigos errados permitidos antes de pedir uno nuevo
VERIFICAR_DOMINIO_CORREO = env_bool('VERIFICAR_DOMINIO_CORREO', True)

# --- Captcha con imágenes reales ---
# Token 1: hCaptcha o Google reCAPTCHA v2 ("No soy un robot" + selección de imágenes reales).
# Token 2: token del servidor, de un solo uso, con tiempo humano mínimo y campo trampa.
# Sin claves propias se usan las claves de PRUEBA oficiales (siempre aprueban, sin imágenes).
CAPTCHA_PROVEEDOR = env('CAPTCHA_PROVEEDOR', 'hcaptcha')      # 'hcaptcha' o 'recaptcha'
_CLAVES_PRUEBA = {
    'hcaptcha': ('10000000-ffff-ffff-ffff-000000000001', '0x0000000000000000000000000000000000000000'),
    'recaptcha': ('6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI', '6LeIxAcTAAAAAGG-vFI1TnRWxMZNFuojJ4WifJWe'),
}
CAPTCHA_SITE_KEY = env('CAPTCHA_SITE_KEY') or _CLAVES_PRUEBA[CAPTCHA_PROVEEDOR][0]   # vacío en .env = claves de prueba
CAPTCHA_SECRET_KEY = env('CAPTCHA_SECRET_KEY') or _CLAVES_PRUEBA[CAPTCHA_PROVEEDOR][1]
if not DEBUG and CAPTCHA_SITE_KEY == _CLAVES_PRUEBA[CAPTCHA_PROVEEDOR][0]:
    raise RuntimeError('Define CAPTCHA_SITE_KEY y CAPTCHA_SECRET_KEY propias en producción.')

CAPTCHA_TIEMPO_MINIMO = 2     # segundos mínimos entre abrir la página y enviar
CAPTCHA_VIGENCIA = 600        # segundos de validez del token del servidor

# ---------------------------------------------------------------------
#  Sesión y cookies
# ---------------------------------------------------------------------
SESSION_COOKIE_NAME = 'sc_sesion'
SESSION_COOKIE_AGE = MINUTOS_SESION * 60
SESSION_SAVE_EVERY_REQUEST = True       # el tiempo se renueva con cada clic
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'

USAR_HTTPS = env_bool('USAR_HTTPS', not DEBUG)
SESSION_COOKIE_SECURE = USAR_HTTPS
CSRF_COOKIE_SECURE = USAR_HTTPS
SECURE_SSL_REDIRECT = USAR_HTTPS
SECURE_HSTS_SECONDS = 31536000 if USAR_HTTPS else 0
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'

# ---------------------------------------------------------------------
#  Correo (en desarrollo se muestra en la consola)
# ---------------------------------------------------------------------
if env('EMAIL_HOST'):
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = env('EMAIL_HOST')                  # ej: smtp.gmail.com / smtp.office365.com
    EMAIL_PORT = int(env('EMAIL_PORT', '587'))
    EMAIL_HOST_USER = env('EMAIL_USUARIO')
    EMAIL_HOST_PASSWORD = env('EMAIL_CLAVE')
    EMAIL_USE_TLS = True
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# Sin servidor de correo y en desarrollo, el código se muestra en pantalla para poder probar.
# En producción (DEBUG=False) nunca se muestra.
MOSTRAR_CODIGO_EN_PANTALLA = DEBUG and EMAIL_BACKEND.endswith('console.EmailBackend')
DEFAULT_FROM_EMAIL = env('EMAIL_REMITENTE', 'Facturación Turística IK <no-responder@facturacionik.local>')

# ---------------------------------------------------------------------
#  Idioma, zona horaria y archivos estáticos
# ---------------------------------------------------------------------
LANGUAGE_CODE = 'es-ec'
TIME_ZONE = 'America/Guayaquil'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# ---------------------------------------------------------------------
#  Asistente de IA (Claude). Sin clave funciona en modo básico (sin conexión).
# ---------------------------------------------------------------------
ANTHROPIC_API_KEY = env('ANTHROPIC_API_KEY', '')
ANTHROPIC_MODEL = env('ANTHROPIC_MODEL', 'claude-sonnet-5-5')
# Alternativa gratuita: Gemini de Google (clave gratis en https://aistudio.google.com). Si hay las dos claves, se usa Claude.
GEMINI_API_KEY = env('GEMINI_API_KEY', '')
GEMINI_MODEL = env('GEMINI_MODEL', '')
# Otra alternativa gratuita y sin tarjeta: Groq (clave en https://console.groq.com/keys)
GROQ_API_KEY = env('GROQ_API_KEY', '')
GROQ_MODEL = env('GROQ_MODEL', '')
