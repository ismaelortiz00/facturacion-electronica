// =====================================================================
//  FACTURACIÓN TURÍSTICA IK · Asistente de IA
//  Pantalla de bienvenida con sugerencias, respuestas que aparecen de a
//  poco, ícono ✦ animado mientras piensa y caja de texto que crece.
//  El historial vive en la sesión del servidor (sigue al cambiar de pantalla).
// =====================================================================
(function () {
    'use strict';
    const chats = document.querySelectorAll('.ia-chat');
    if (!chats.length) return;
    const csrf = document.querySelector('meta[name="csrf-token"]').content;
    const sinMovimiento = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // Texto seguro: escapa HTML; permite **negritas**, viñetas «• » y saltos de línea
    function formato(texto) {
        const div = document.createElement('div');
        div.textContent = texto;
        return div.innerHTML
            .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
            .replace(/(^|\n)• (.*?)(?=\n|$)/g, '$1<span class="ia-vineta">$2</span>')
            .replace(/\n?(<span class="ia-vineta">.*?<\/span>)\n?/g, '$1')   // la viñeta ya es un bloque: sin saltos extra
            .replace(/\n/g, '<br>');
    }

    chats.forEach(function (chat) {
        const cuerpo = chat.querySelector('[data-ia-cuerpo]');
        const lista = chat.querySelector('[data-ia-mensajes]');
        const bienvenida = chat.querySelector('[data-ia-bienvenida]');
        const form = chat.querySelector('[data-ia-form]');
        const campo = form.querySelector('textarea');
        const boton = form.querySelector('.ia-enviar');
        const modo = chat.querySelector('[data-ia-modo]');
        let cargado = false, ocupado = false;

        function bajar() { cuerpo.scrollTop = cuerpo.scrollHeight; }
        function conversando(si) { chat.classList.toggle('ia-conversando', si); bienvenida.hidden = si; }

        function mensajeYo(texto) {
            const m = document.createElement('div');
            m.className = 'ia-msg ia-yo';
            m.textContent = texto;
            lista.appendChild(m);
            bajar();
        }

        function mensajeBot(texto, acciones, herramientas, aviso, animar) {
            const m = document.createElement('div');
            m.className = 'ia-msg ia-bot';
            m.innerHTML = '<span class="ia-avatar" aria-hidden="true">✦</span><div class="ia-texto"></div>';
            const caja = m.querySelector('.ia-texto');
            lista.appendChild(m);
            const extras = function () {
                (acciones || []).forEach(function (a) {
                    if (!a.url || a.url.charAt(0) !== '/') return;   // solo enlaces internos
                    const b = document.createElement('a');
                    b.className = 'ia-accion';
                    b.href = a.url;
                    b.textContent = a.texto + ' →';
                    caja.appendChild(b);
                });
                if ((herramientas && herramientas.length) || aviso) {
                    const pie = document.createElement('div');
                    pie.className = 'ia-pie';
                    if (aviso) { const s = document.createElement('span'); s.className = 'ia-aviso'; s.textContent = aviso; pie.appendChild(s); }
                    (herramientas || []).filter(function (h, i, a) { return a.indexOf(h) === i; }).forEach(function (h) {
                        const s = document.createElement('span'); s.className = 'ia-herramienta'; s.textContent = h.replace(/_/g, ' ');
                        pie.appendChild(s);
                    });
                    caja.appendChild(pie);
                }
                bajar();
            };
            if (!animar || sinMovimiento) { caja.innerHTML = formato(texto); extras(); bajar(); return; }
            // Aparece palabra por palabra, como una respuesta que se va escribiendo
            const palabras = texto.split(/(\s+)/);
            let i = 0;
            const paso = Math.max(1, Math.ceil(palabras.length / 60));
            (function escribir() {
                i = Math.min(i + paso, palabras.length);
                caja.innerHTML = formato(palabras.slice(0, i).join(''));
                bajar();
                if (i < palabras.length) setTimeout(escribir, 18); else extras();
            })();
        }

        function pensando() {
            const m = document.createElement('div');
            m.className = 'ia-msg ia-bot ia-pensando';
            m.innerHTML = '<span class="ia-avatar ia-girando" aria-hidden="true">✦</span>' +
                '<div class="ia-texto"><span class="ia-brillo"></span><span class="ia-brillo"></span><span class="ia-brillo corta"></span></div>';
            lista.appendChild(m);
            bajar();
            return m;
        }

        var NOMBRES = {claude: 'Claude', gemini: 'Gemini', groq: 'Groq'};
        function ponerModo(motor) {
            if (!modo) return;
            var nombre = NOMBRES[motor];
            modo.textContent = nombre || 'modo básico';
            modo.classList.toggle('ia-modo-basico', !nombre);
            modo.title = nombre ? 'Conectado a ' + nombre : 'Sin clave de IA: responde preguntas frecuentes';
        }

        chat.ajustar = function () { ajustar(); };
        chat.cargar = function () {
            if (cargado) return;
            cargado = true;
            fetch(chat.dataset.iaHistorial, { credentials: 'same-origin' }).then(function (r) { return r.json(); })
                .then(function (d) {
                    ponerModo(d.motor);
                    if (d.mensajes.length) conversando(true);
                    d.mensajes.forEach(function (m) {
                        if (m.rol === 'user') mensajeYo(m.texto); else mensajeBot(m.texto, m.acciones, [], '', false);
                    });
                }).catch(function () { /* sin historial */ });
        };

        function ajustar() {
            if (campo.offsetParent !== null) {   // si el panel está oculto no se puede medir
                campo.style.height = 'auto';
                campo.style.height = Math.min(Math.max(campo.scrollHeight, 24), 160) + 'px';
            }
            boton.disabled = ocupado || !campo.value.trim();
        }

        function enviar(texto) {
            if (!texto.trim() || ocupado) return;
            ocupado = true;
            conversando(true);
            mensajeYo(texto);
            campo.value = '';
            ajustar();
            const espera = pensando();
            fetch(chat.dataset.iaUrl, {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
                body: JSON.stringify({ texto: texto, pagina: window.location.pathname })
            }).then(function (r) { return r.json(); }).then(function (d) {
                espera.remove();
                mensajeBot(d.texto || d.error || 'Sin respuesta.', d.acciones, d.herramientas, d.aviso, true);
                ponerModo(d.modo);
            }).catch(function () {
                espera.remove();
                mensajeBot('No pude conectarme con el servidor. Revisa tu conexión e intenta de nuevo.', [], [], '', false);
            }).finally(function () { ocupado = false; ajustar(); campo.focus(); });
        }

        form.addEventListener('submit', function (e) { e.preventDefault(); enviar(campo.value); });
        campo.addEventListener('input', ajustar);
        campo.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); enviar(campo.value); }
        });
        chat.querySelectorAll('[data-ia-sugerencias] button').forEach(function (b) {
            b.addEventListener('click', function () {
                if (b.dataset.rellenar) {
                    // No se envía nada: se deja el inicio de la orden para que el usuario ponga sus datos
                    campo.value = b.dataset.rellenar;
                    campo.placeholder = 'Ej. Factura para Luis Mora: 2 tour Mindo y 1 almuerzo';
                    ajustar();
                    campo.focus();
                    campo.setSelectionRange(campo.value.length, campo.value.length);
                    return;
                }
                enviar(b.dataset.texto);
            });
        });
        chat.querySelector('[data-ia-limpiar]').addEventListener('click', function () {
            fetch(chat.dataset.iaUrl, { method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf }, body: JSON.stringify({ limpiar: true }) });
            lista.textContent = '';
            conversando(false);
            campo.focus();
        });
        ajustar();
        if (chat.closest('.ia-pagina')) chat.cargar();
    });

    // Panel flotante
    const panel = document.getElementById('iaPanel');
    const abrir = document.getElementById('iaAbrir');
    if (panel && abrir) {
        const chat = panel.querySelector('.ia-chat');
        const cerrar = function () { panel.hidden = true; abrir.hidden = false; };
        abrir.addEventListener('click', function () {
            panel.hidden = false;
            abrir.hidden = true;
            chat.cargar();
            chat.ajustar();
            panel.querySelector('textarea').focus();
        });
        panel.querySelector('[data-ia-cerrar]').addEventListener('click', cerrar);
        document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !panel.hidden) cerrar(); });
    }
})();
