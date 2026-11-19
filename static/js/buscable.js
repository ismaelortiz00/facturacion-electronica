// =====================================================================
//  SISTEMA CONTABLE IK · Listas con buscador
//  Convierte <select data-buscar> en un campo donde se escribe parte del
//  código o del nombre (sin importar tildes) y se elige con el mouse o con
//  las flechas + Enter. El <select> original sigue enviando el valor.
// =====================================================================
(function () {
    'use strict';
    const listos = new WeakSet();

    function normal(t) {
        return (t || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
    }

    function preparar(select) {
        if (listos.has(select)) return;
        // Si viene de una fila clonada, quitar el envoltorio copiado sin eventos
        const viejo = select.closest('.buscable');
        if (viejo) { viejo.parentNode.insertBefore(select, viejo); viejo.remove(); }
        listos.add(select);

        const caja = document.createElement('div');
        caja.className = 'buscable';
        const campo = document.createElement('input');
        campo.type = 'text';
        campo.className = select.className + ' buscable-campo';
        campo.autocomplete = 'off';
        campo.setAttribute('role', 'combobox');
        campo.setAttribute('aria-expanded', 'false');
        const vacia = select.querySelector('option[value=""]');
        campo.placeholder = (vacia ? vacia.textContent : 'Escribe para buscar…').replace('…', '') + ' (escribe para buscar)';
        if (select.getAttribute('aria-label')) campo.setAttribute('aria-label', select.getAttribute('aria-label'));
        const lista = document.createElement('ul');
        lista.className = 'buscable-lista';
        lista.setAttribute('role', 'listbox');
        lista.hidden = true;
        select.parentNode.insertBefore(caja, select);
        caja.appendChild(select);
        caja.appendChild(campo);
        document.body.appendChild(lista);
        select.classList.add('buscable-oculto');
        select.tabIndex = -1;
        if (select.required) { campo.required = true; select.required = false; }

        let activo = -1, visibles = [];

        function opciones() {
            return Array.from(select.options).filter(function (o) { return o.value !== ''; }).map(function (o) {
                return { valor: o.value, texto: o.textContent.trim(), grupo: o.parentNode.tagName === 'OPTGROUP' ? o.parentNode.label : '' };
            });
        }
        function sincronizar() {
            const o = select.selectedOptions[0];
            campo.value = o && o.value ? o.textContent.trim() : '';
        }
        function ubicar() {
            const r = campo.getBoundingClientRect();
            lista.style.left = r.left + 'px';
            lista.style.top = (r.bottom + 4) + 'px';
            lista.style.minWidth = Math.max(r.width, 280) + 'px';
        }
        function pintar(filtro) {
            const palabras = normal(filtro).split(/\s+/).filter(Boolean);
            visibles = opciones().filter(function (o) {
                const t = normal(o.texto + ' ' + o.grupo);
                return palabras.every(function (p) { return t.indexOf(p) !== -1; });
            }).slice(0, 80);
            lista.textContent = '';
            let grupo = null;
            visibles.forEach(function (o, i) {
                if (o.grupo && o.grupo !== grupo) {
                    grupo = o.grupo;
                    const g = document.createElement('li');
                    g.className = 'buscable-grupo';
                    g.textContent = grupo;
                    lista.appendChild(g);
                }
                const li = document.createElement('li');
                li.textContent = o.texto;
                li.setAttribute('role', 'option');
                li.dataset.i = i;
                if (o.valor === select.value) li.classList.add('elegido');
                li.addEventListener('mousedown', function (e) { e.preventDefault(); elegir(i); });
                lista.appendChild(li);
            });
            if (!visibles.length) {
                const li = document.createElement('li');
                li.className = 'buscable-vacio';
                li.textContent = 'No hay coincidencias';
                lista.appendChild(li);
            }
            activo = visibles.length ? 0 : -1;
            marcar();
            ubicar();
            lista.hidden = false;
            campo.setAttribute('aria-expanded', 'true');
        }
        function marcar() {
            lista.querySelectorAll('li[data-i]').forEach(function (li) {
                li.classList.toggle('activo', Number(li.dataset.i) === activo);
                if (Number(li.dataset.i) === activo) li.scrollIntoView({ block: 'nearest' });
            });
        }
        function cerrar() { lista.hidden = true; campo.setAttribute('aria-expanded', 'false'); }
        function elegir(i) {
            const o = visibles[i];
            if (!o) return;
            select.value = o.valor;
            campo.value = o.texto;
            campo.setCustomValidity('');
            cerrar();
            select.dispatchEvent(new Event('change', { bubbles: true }));
        }

        campo.addEventListener('focus', function () { campo.select(); pintar(''); });
        campo.addEventListener('input', function () { pintar(campo.value); });
        campo.addEventListener('keydown', function (e) {
            if (lista.hidden && (e.key === 'ArrowDown' || e.key === 'Enter')) { pintar(''); e.preventDefault(); return; }
            if (e.key === 'ArrowDown') { activo = Math.min(activo + 1, visibles.length - 1); marcar(); e.preventDefault(); }
            else if (e.key === 'ArrowUp') { activo = Math.max(activo - 1, 0); marcar(); e.preventDefault(); }
            else if (e.key === 'Enter') { if (activo >= 0) { elegir(activo); e.preventDefault(); } }
            else if (e.key === 'Escape') { sincronizar(); cerrar(); }
            else if (e.key === 'Tab' && !lista.hidden && activo >= 0 && campo.value && campo.value !== (select.selectedOptions[0] || {}).textContent) { elegir(activo); }
        });
        campo.addEventListener('blur', function () {
            cerrar();
            if (!campo.value.trim()) {  // borrar el texto deja la lista sin elegir
                if (select.value) { select.value = ''; select.dispatchEvent(new Event('change', { bubbles: true })); }
            } else {
                sincronizar();
            }
        });
        select.addEventListener('change', sincronizar);
        window.addEventListener('resize', function () { if (!lista.hidden) ubicar(); });
        document.addEventListener('scroll', function () { if (!lista.hidden) ubicar(); }, true);
        sincronizar();
    }

    function revisar(raiz) {
        (raiz.querySelectorAll ? raiz : document).querySelectorAll('select[data-buscar]').forEach(preparar);
        if (raiz.matches && raiz.matches('select[data-buscar]')) preparar(raiz);
    }
    revisar(document);
    // Filas nuevas (Agregar línea) también reciben el buscador
    new MutationObserver(function (cambios) {
        cambios.forEach(function (c) { c.addedNodes.forEach(function (n) { if (n.nodeType === 1) revisar(n); }); });
    }).observe(document.body, { childList: true, subtree: true });
    window.IKBuscable = { revisar: revisar };
})();
