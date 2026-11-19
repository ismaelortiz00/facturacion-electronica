# Facturación Turística IK

Sistema web de facturación electrónica para negocios turísticos con aplicación automática del
IVA diferenciado en feriados nacionales, validado ante el SRI, y asistente de inteligencia artificial.

## Qué hace
1. **Inicio de sesión seguro:** cédula o correo, contraseña con Argon2, captcha de dos tokens y bloqueo tras 3 intentos.
2. **Clientes:** valida cédula y RUC (algoritmos del Registro Civil y del SRI); turistas extranjeros con pasaporte.
3. **Servicios:** habitaciones, tours, alimentos, transporte… marcados como turísticos o no.
4. **Facturas:** el IVA se calcula solo. Si la fecha cae en un feriado con decreto y el servicio es turístico → IVA reducido (8 %); si no → tarifa general (15 %).
5. **SRI:** firma XAdES-BES con el .p12 del negocio, envío, autorización y reintentos sin internet.
6. **Correo al cliente:** PDF (RIDE) y XML.
7. **Notas de crédito** con el mismo IVA de la factura original.
8. **Cobros** y lista de quién debe.
9. **Reportes:** ventas por día, por servicio y por tarifa; IVA a declarar; ahorro de los turistas por los decretos.
10. **Asistente de IA:** consulta datos reales, explica el IVA, prepara facturas en borrador y avisa de problemas.

## Instalación (Windows)
```
cd D:\facturacion_turistica
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.ejemplo .env          (y llena tus claves)
python manage.py migrate
python manage.py createsuperuser
python manage.py datos_demo     (opcional: negocio de ejemplo con 100 facturas)
python manage.py runserver
```
Entra a http://127.0.0.1:8000/ con la cédula y la contraseña del superusuario.

## Asistente de IA
- Gratis con Groq (sin tarjeta): pon `GROQ_API_KEY=...` en `.env` (la clave se crea en https://console.groq.com/keys).
- Gratis con Gemini: pon `GEMINI_API_KEY=...` en `.env` (la clave se crea en https://aistudio.google.com → Get API key).
- Con Claude: pon `ANTHROPIC_API_KEY=...` en `.env` (se crea en https://console.anthropic.com). Usa Claude con herramientas.
- Sin clave o sin internet: funciona en **modo básico** (entiende las preguntas frecuentes y prepara facturas por texto).
- La IA nunca emite ni anula: deja borradores y una persona confirma. Todo queda en la bitácora.

## Facturación electrónica
1. Configuración → Firma electrónica: sube el .p12 y deja el ambiente en **Pruebas**.
2. Emite una factura y revisa en «Autorizaciones del SRI» que diga «Autorizado».
3. Cuando funcione en pruebas, cambia a **Producción**.

## Feriados
Los decretos de 2026 ya vienen cargados. Cuando el Gobierno publique uno nuevo, el administrador lo registra
en «Feriados e IVA» (fechas y porcentaje) y desde ese día se aplica solo.

## Pruebas automáticas
```
python manage.py test
```
