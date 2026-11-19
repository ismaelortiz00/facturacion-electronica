// =====================================================================
//  SISTEMA CONTABLE - Lógica del navegador
//  · Captcha: hCaptcha/reCAPTCHA (token 1) + token del servidor (token 2)
//  · Envío de formularios por AJAX con token CSRF
//  · Botones "plastilina": se hunden solo donde se presiona y sueltan
//    ondas como agua
//  · Validación de cédula, requisitos de contraseña y contador de bloqueo
//  (La validación real siempre se repite en el servidor.)
// =====================================================================
(function () {
    'use strict';

    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';

    // ---------------------------------------------------------------
    //  Envío POST que devuelve JSON
    // ---------------------------------------------------------------
    async function enviar(url, datos) {
        try {
            const r = await fetch(url, {
                method: 'POST',
                body: datos,
                credentials: 'same-origin',
                headers: { 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': csrf }
            });
            if (r.redirected) { window.location.href = r.url; return {}; }
            return await r.json();
        } catch (e) {
            return { success: false, message: 'No se pudo conectar con el servidor.' };
        }
    }

    function mostrarMensaje(texto, tipo) {
        const caja = document.getElementById('mensaje');
        if (!caja) return;
        caja.textContent = texto;
        caja.className = 'mensaje ' + (tipo || 'error');
        caja.hidden = false;
    }

    // ---------------------------------------------------------------
    //  CAPTCHA
    // ---------------------------------------------------------------
    const Captcha = {
        caja: document.getElementById('captcha'),

        api: function () {
            // window.hcaptcha o window.grecaptcha, según el proveedor configurado
            return this.caja ? window[this.caja.dataset.proveedor] : null;
        },

        listo: function () {
            const api = this.api();
            try { return !!(api && api.getResponse()); } catch (e) { return false; }
        },

        reiniciar: function (nuevoToken2) {
            const api = this.api();
            try { if (api) api.reset(); } catch (e) { /* el widget aún no carga */ }
            if (nuevoToken2) document.getElementById('token2').value = nuevoToken2;
        },

        vigilarCarga: function () {
            if (!this.caja) return;
            // Si a los 8 segundos el widget no apareció, se avisa al usuario
            setTimeout(function () {
                if (!Captcha.api()) document.getElementById('captchaAviso').hidden = false;
            }, 8000);
        }
    };

    // ---------------------------------------------------------------
    //  Formularios AJAX
    // ---------------------------------------------------------------
    let temporizador = null;

    function cuentaRegresiva(segundos, boton) {
        clearInterval(temporizador);
        boton.disabled = true;
        const texto = boton.dataset.texto || boton.textContent;
        boton.dataset.texto = texto;
        temporizador = setInterval(function () {
            segundos--;
            const m = Math.floor(segundos / 60), s = segundos % 60;
            boton.querySelector('.btn-texto').textContent = 'Bloqueado ' + m + ':' + String(s).padStart(2, '0');
            if (segundos <= 0) {
                clearInterval(temporizador);
                boton.disabled = false;
                boton.querySelector('.btn-texto').textContent = texto.trim();
            }
        }, 1000);
    }

    document.querySelectorAll('form[data-ajax]').forEach(function (form) {
        form.addEventListener('submit', async function (ev) {
            ev.preventDefault();
            const boton = form.querySelector('button[type="submit"]');

            if (form.querySelector('.captcha') && !Captcha.listo()) {
                mostrarMensaje('Marca la casilla "No soy un robot" y completa la selección de imágenes.', 'error');
                return;
            }
            const conf = form.querySelector('#confirmar');
            if (conf && conf.value !== form.querySelector('#clave').value) {
                mostrarMensaje('Las contraseñas no coinciden.', 'error');
                return;
            }

            boton.disabled = true; // evita doble clic y envíos duplicados
            const r = await enviar(form.action, new FormData(form));

            if (r.success && r.redirect) {
                mostrarMensaje(r.message, 'ok');
                window.location.href = r.redirect;
                return;
            }

            mostrarMensaje(r.message || 'Ocurrió un error.', r.success ? 'ok' : 'error');
            if (r.reiniciar_captcha) Captcha.reiniciar(r.token2);
            if (r.success && r.verificar && form.hasAttribute('data-paso-codigo')) PasoCodigo.mostrar(r);
            if (!r.success && form.id === 'formCodigo') PasoCodigo.limpiar();
            if (r.success && form.hasAttribute('data-limpiar')) form.reset();

            const clave = form.querySelector('input[name="clave"]');
            if (!r.success && clave && form.id === 'formLogin') clave.value = '';

            if (r.segundos_bloqueo) cuentaRegresiva(r.segundos_bloqueo, boton);
            else boton.disabled = false;
        });
    });

    // ---------------------------------------------------------------
    //  BOTONES PLASTILINA
    //  Al presionar: aparece un hundimiento cóncavo justo bajo el dedo
    //  o el puntero; si se arrastra, el hundimiento lo sigue.
    //  Al soltar: la superficie vuelve con rebote y salen ondas de agua.
    // ---------------------------------------------------------------
    function prepararBoton(b) {
        if (b.dataset.plastilina) return;
        b.dataset.plastilina = '1';
        b.classList.add('plastilina');
        if (!b.querySelector('.btn-texto')) {
            const span = document.createElement('span');
            span.className = 'btn-texto';
            while (b.firstChild) span.appendChild(b.firstChild);
            b.appendChild(span);
        }
        const hueco = document.createElement('span');
        hueco.className = 'hueco';
        b.appendChild(hueco);
    }

    function ubicar(b, x, y) {
        b.style.setProperty('--x', x + 'px');
        b.style.setProperty('--y', y + 'px');
        // el texto se inclina un poco hacia el punto presionado
        const r = b.getBoundingClientRect();
        b.style.setProperty('--tx', ((x - r.width / 2) / r.width * 3).toFixed(2) + 'px');
        b.style.setProperty('--ty', ((y - r.height / 2) / r.height * 2 + 1.5).toFixed(2) + 'px');
    }

    function onda(b, x, y, retraso) {
        const o = document.createElement('span');
        o.className = 'onda';
        o.style.left = x + 'px';
        o.style.top = y + 'px';
        o.style.animationDelay = (retraso || 0) + 'ms';
        b.appendChild(o);
        o.addEventListener('animationend', function () { o.remove(); });
    }

    function presionar(b, x, y) {
        ubicar(b, x, y);
        b.classList.remove('soltando');
        b.classList.add('presionado');
    }

    function soltar(b) {
        if (!b.classList.contains('presionado')) return;
        b.classList.remove('presionado');
        b.classList.add('soltando');
        const x = parseFloat(b.style.getPropertyValue('--x')) || b.offsetWidth / 2;
        const y = parseFloat(b.style.getPropertyValue('--y')) || b.offsetHeight / 2;
        onda(b, x, y, 0);
        onda(b, x, y, 120);
        setTimeout(function () { b.classList.remove('soltando'); }, 650);
    }

    document.querySelectorAll('.btn, .ver-clave').forEach(prepararBoton);

    // ---------------------------------------------------------------
    //  BOTÓN INTERRUPTOR (estilo "power switch")
    //  Apagado: píldora oscura con la estrella tenue.
    //  Encendido (mouse encima, foco o toque): se ilumina desde abajo,
    //  brilla alrededor, la estrella destella y suben partículas de luz.
    // ---------------------------------------------------------------
    const ESTRELLA = '<svg class="destello" viewBox="0 0 24 24" aria-hidden="true">' +
        '<path d="M10 2.5l1.9 5.6 5.6 1.9-5.6 1.9L10 17.5l-1.9-5.6L2.5 10l5.6-1.9z"/>' +
        '<path d="M18.5 13.5l.9 2.6 2.6.9-2.6.9-.9 2.6-.9-2.6-2.6-.9 2.6-.9z"/></svg>';

    function prepararInterruptor(b) {
        const envoltura = document.createElement('span');
        envoltura.className = 'interruptor';
        b.parentNode.insertBefore(envoltura, b);
        envoltura.appendChild(b);

        b.querySelector('.btn-texto').insertAdjacentHTML('afterbegin', ESTRELLA);

        for (let i = 0; i < 18; i++) {            // partículas de luz
            const c = document.createElement('span');
            c.className = 'chispa';
            c.style.left = (4 + Math.random() * 92) + '%';
            c.style.top = (35 + Math.random() * 45) + '%';
            c.style.setProperty('--t', (2.2 + Math.random() * 2.4).toFixed(2) + 's');
            c.style.setProperty('--d', (-Math.random() * 4).toFixed(2) + 's');
            c.style.setProperty('--s', (1.5 + Math.random() * 2.5).toFixed(1) + 'px');
            c.style.setProperty('--dx', ((Math.random() - .5) * 30).toFixed(0) + 'px');
            envoltura.appendChild(c);
        }

        let apagar = null;
        const encender = function () { if (!b.disabled) { clearTimeout(apagar); envoltura.classList.add('encendido'); } };
        const apagarYa = function () { if (document.activeElement !== b) envoltura.classList.remove('encendido'); };

        b.addEventListener('pointerenter', function (e) { if (e.pointerType === 'mouse') encender(); });
        b.addEventListener('pointerleave', function (e) { if (e.pointerType === 'mouse') apagarYa(); });
        b.addEventListener('focus', encender);
        b.addEventListener('blur', function () { envoltura.classList.remove('encendido'); });
        // En celulares no hay "pasar el mouse": se enciende al tocar y se apaga solo
        b.addEventListener('pointerdown', function (e) {
            if (e.pointerType !== 'mouse') { encender(); clearTimeout(apagar); apagar = setTimeout(apagarYa, 1600); }
        });
    }

    document.querySelectorAll('.btn-principal').forEach(prepararInterruptor);

    document.addEventListener('pointerdown', function (e) {
        const b = e.target.closest('.plastilina');
        if (!b || b.disabled || e.button > 0) return;
        const r = b.getBoundingClientRect();
        presionar(b, e.clientX - r.left, e.clientY - r.top);

        const mover = function (ev) {
            const rr = b.getBoundingClientRect();
            ubicar(b, Math.min(Math.max(ev.clientX - rr.left, 0), rr.width),
                      Math.min(Math.max(ev.clientY - rr.top, 0), rr.height));
        };
        const fin = function () {
            soltar(b);
            window.removeEventListener('pointermove', mover);
            window.removeEventListener('pointerup', fin);
            window.removeEventListener('pointercancel', fin);
        };
        window.addEventListener('pointermove', mover);
        window.addEventListener('pointerup', fin);
        window.addEventListener('pointercancel', fin);
    });

    // Con teclado (Enter o Espacio) el hundimiento sale en el centro
    document.addEventListener('keydown', function (e) {
        const b = document.activeElement;
        if (b && b.classList && b.classList.contains('plastilina') && (e.key === 'Enter' || e.key === ' ') && !e.repeat) {
            presionar(b, b.offsetWidth / 2, b.offsetHeight / 2);
        }
    });
    document.addEventListener('keyup', function (e) {
        const b = document.activeElement;
        if (b && b.classList && b.classList.contains('plastilina') && (e.key === 'Enter' || e.key === ' ')) soltar(b);
    });

    // ---------------------------------------------------------------
    //  PASO DEL CÓDIGO DE 6 DÍGITOS (misma página, sin enlaces)
    // ---------------------------------------------------------------
    const PasoCodigo = {
        caja: document.getElementById('pasoCodigo'),

        digitos: function () { return Array.from(document.querySelectorAll('.codigo-digito')); },

        actualizar: function () {
            document.getElementById('codigoValor').value = this.digitos().map(function (d) { return d.value; }).join('');
        },

        mostrar: function (r) {
            if (!this.caja) return;
            document.getElementById('pasoFormulario').hidden = true;
            this.caja.hidden = false;
            document.getElementById('codigoCorreo').value = r.correo;
            document.getElementById('codigoDestino').textContent = r.correo;
            this.mostrarDesarrollo(r.codigo_desarrollo);
            this.limpiar();
        },

        mostrarDesarrollo: function (codigo) {
            const aviso = document.getElementById('codigoDesarrollo');
            aviso.hidden = !codigo;
            if (codigo) aviso.querySelector('strong').textContent = codigo;
        },

        limpiar: function () {
            this.digitos().forEach(function (d) { d.value = ''; });
            this.actualizar();
            const primero = this.digitos()[0];
            if (primero) primero.focus();
        },

        iniciar: function () {
            if (!this.caja) return;
            const yo = this;
            const lista = this.digitos();
            const form = document.getElementById('formCodigo');

            lista.forEach(function (d, i) {
                d.addEventListener('input', function () {
                    d.value = d.value.replace(/\D/g, '').slice(-1);
                    yo.actualizar();
                    if (d.value && i < lista.length - 1) lista[i + 1].focus();
                    if (document.getElementById('codigoValor').value.length === 6) form.requestSubmit();
                });
                d.addEventListener('keydown', function (e) {
                    if (e.key === 'Backspace' && !d.value && i > 0) lista[i - 1].focus();
                });
                d.addEventListener('paste', function (e) {   // pegar el código completo
                    const texto = (e.clipboardData.getData('text') || '').replace(/\D/g, '').slice(0, 6);
                    if (!texto) return;
                    e.preventDefault();
                    lista.forEach(function (x, k) { x.value = texto[k] || ''; });
                    yo.actualizar();
                    if (texto.length === 6) form.requestSubmit();
                });
            });

            document.getElementById('reenviarCodigo').addEventListener('click', async function (e) {
                e.preventDefault();
                const datos = new FormData();
                datos.append('correo', document.getElementById('codigoCorreo').value);
                const r = await enviar(yo.caja.dataset.urlReenviar, datos);
                mostrarMensaje(r.message || 'Ocurrió un error.', r.success ? 'ok' : 'error');
                if (r.success) { yo.mostrarDesarrollo(r.codigo_desarrollo); yo.limpiar(); }
            });
        }
    };
    PasoCodigo.iniciar();

    // ---------------------------------------------------------------
    //  Cédula ecuatoriana (módulo 10) mientras se escribe
    // ---------------------------------------------------------------
    function validarCedula(c) {
        if (!/^\d{10}$/.test(c)) return 'Debe tener 10 números.';
        const prov = parseInt(c.substring(0, 2), 10);
        if (!((prov >= 1 && prov <= 24) || prov === 30)) return 'Código de provincia inválido.';
        if (parseInt(c[2], 10) > 5) return 'El tercer dígito debe ser de 0 a 5.';
        const coef = [2, 1, 2, 1, 2, 1, 2, 1, 2];
        let suma = 0;
        for (let i = 0; i < 9; i++) {
            let p = parseInt(c[i], 10) * coef[i];
            if (p > 9) p -= 9;
            suma += p;
        }
        return (10 - (suma % 10)) % 10 === parseInt(c[9], 10) ? '' : 'Dígito verificador incorrecto.';
    }

    function avisoCampo(input, ayuda, error, textoOk) {
        ayuda.textContent = error || textoOk;
        ayuda.className = 'ayuda ' + (error ? 'mal' : 'bien');
        input.classList.toggle('invalido', !!error);
    }

    const cedula = document.querySelector('[data-cedula]');
    if (cedula) {
        const ayuda = document.getElementById('ayudaCedula');
        cedula.addEventListener('input', function () {
            cedula.value = cedula.value.replace(/\D/g, '').slice(0, 10);
            if (cedula.value.length < 10) { ayuda.textContent = ''; cedula.classList.remove('invalido'); return; }
            avisoCampo(cedula, ayuda, validarCedula(cedula.value), '✓ Cédula válida');
        });
    }

    const usuario = document.querySelector('[data-usuario]');
    if (usuario) {
        const ayuda = document.getElementById('ayudaUsuario');
        usuario.addEventListener('blur', function () {
            const v = usuario.value.trim();
            if (v === '') { ayuda.textContent = ''; return; }
            if (v.indexOf('@') !== -1) {
                const ok = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v);
                avisoCampo(usuario, ayuda, ok ? '' : 'Correo con formato inválido.', '✓ Correo');
            } else {
                avisoCampo(usuario, ayuda, validarCedula(v), '✓ Cédula válida');
            }
        });
    }

    // ---------------------------------------------------------------
    //  Requisitos de contraseña y botón Ver
    // ---------------------------------------------------------------
    const fuerza = document.querySelector('[data-fuerza]');
    if (fuerza) {
        const reglas = {
            largo:   function (v) { return v.length >= 8; },
            mayus:   function (v) { return /[A-Z]/.test(v); },
            minus:   function (v) { return /[a-z]/.test(v); },
            numero:  function (v) { return /\d/.test(v); },
            simbolo: function (v) { return /[^A-Za-z0-9]/.test(v); }
        };
        fuerza.addEventListener('input', function () {
            document.querySelectorAll('#requisitos li').forEach(function (li) {
                li.classList.toggle('cumple', reglas[li.dataset.regla](fuerza.value));
            });
        });
    }

    document.querySelectorAll('.ver-clave').forEach(function (b) {
        b.addEventListener('click', function () {
            const input = document.getElementById(b.dataset.ver);
            const ver = input.type === 'password';
            input.type = ver ? 'text' : 'password';
            b.querySelector('.btn-texto').textContent = ver ? 'Ocultar' : 'Ver';
        });
    });

    Captcha.vigilarCarga();
})();
