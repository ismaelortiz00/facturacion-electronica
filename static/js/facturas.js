// =====================================================================
//  FACTURACIÓN TURÍSTICA IK · Pantallas
//  Filas clicables, confirmaciones, menú, factura con IVA en vivo y cobros.
//  El servidor vuelve a calcular todo al guardar: esto solo ayuda a ver.
// =====================================================================
(function () {
    'use strict';

    document.querySelectorAll('tr[data-href]').forEach(function (fila) {
        fila.addEventListener('click', function (e) {
            if (e.target.closest('a, button, input, select, form')) return;
            window.location.href = fila.dataset.href;
        });
    });
    document.querySelectorAll('[data-enviar-al-cambiar]').forEach(function (s) {
        s.addEventListener('change', function () { s.form.submit(); });
    });
    document.querySelectorAll('form[data-confirmar]').forEach(function (f) {
        f.addEventListener('submit', function (e) { if (!window.confirm(f.dataset.confirmar)) e.preventDefault(); });
    });

    // Menú por grupos: abre el de la página actual y recuerda los que abriste
    let guardados = {};
    try { guardados = JSON.parse(localStorage.getItem('ik-menu') || '{}'); } catch (e) { guardados = {}; }
    document.querySelectorAll('.menu-grupo').forEach(function (g) {
        g.open = Boolean(g.querySelector('a.activo')) || guardados[g.dataset.grupo] !== false;
        g.addEventListener('toggle', function () {
            guardados[g.dataset.grupo] = g.open;
            try { localStorage.setItem('ik-menu', JSON.stringify(guardados)); } catch (e) { /* sin almacenamiento */ }
        });
    });

    function centavos(t) { const n = parseFloat(String(t || '').replace(/,/g, '')); return isNaN(n) ? 0 : Math.round(n * 100); }
    function dinero(c) { return (c / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }

    // -----------------------------------------------------------------
    //  Factura: IVA según la fecha, el servicio y los decretos
    // -----------------------------------------------------------------
    const form = document.getElementById('formFactura');
    if (form) {
        const tasas = JSON.parse(document.getElementById('tasasIva').textContent);
        const cuerpo = form.querySelector('tbody');
        const campoFecha = form.querySelector('[name="fecha"]');
        const aviso = document.getElementById('avisoIva');
        const esNota = form.dataset.tipo === 'nota_credito';

        function reglas(fecha) {
            let general = 15;
            tasas.generales.forEach(function (t) { if (t[0] <= fecha) general = t[1]; });
            const decreto = tasas.decretos.find(function (d) { return d[0] <= fecha && fecha <= d[1]; }) || null;
            return { general: general, decreto: decreto };
        }

        function recalcular() {
            const fecha = campoFecha.value || new Date().toISOString().slice(0, 10);
            const r = reglas(fecha);
            let base = 0, iva = 0, usaDecreto = false;
            const grupos = {};
            cuerpo.querySelectorAll('tr.linea').forEach(function (f) {
                const op = f.querySelector('[name="servicio_id"]').selectedOptions[0];
                const cant = parseFloat(f.querySelector('[name="cantidad"]').value) || 0;
                const precio = parseFloat(String(f.querySelector('[name="precio"]').value).replace(/,/g, '')) || 0;
                const desc = centavos(f.querySelector('[name="descuento"]').value);
                const sub = Math.max(Math.round(cant * precio * 100) - desc, 0);
                let pct = 0, etiqueta = '';
                if (op && op.value) {
                    if (esNota && f.dataset.pct) { pct = parseFloat(f.dataset.pct); etiqueta = pct + ' % (de la factura)'; }
                    else if (op.dataset.tarifa !== 'general') { pct = 0; etiqueta = op.dataset.tarifaTexto; }
                    else if (op.dataset.turistico === '1' && r.decreto) { pct = r.decreto[2]; etiqueta = pct + ' % · feriado'; usaDecreto = true; }
                    else { pct = r.general; etiqueta = pct + ' %'; }
                }
                f.querySelector('[data-iva-linea]').textContent = op && op.value ? etiqueta : '—';
                f.querySelector('[data-iva-linea]').classList.toggle('iva-reducido', etiqueta.indexOf('·') !== -1);
                f.querySelector('[data-subtotal]').textContent = dinero(sub);
                base += sub;
                grupos[pct] = (grupos[pct] || 0) + sub;
            });
            Object.keys(grupos).forEach(function (p) { iva += Math.round(grupos[p] * p / 100); });
            form.querySelector('[data-base]').textContent = dinero(base);
            form.querySelector('[data-iva]').textContent = dinero(iva);
            form.querySelector('[data-total]').textContent = dinero(base + iva);
            if (aviso && !esNota) {
                aviso.hidden = false;
                aviso.className = 'aviso-iva ' + (r.decreto ? 'feriado' : '');
                aviso.textContent = r.decreto
                    ? 'Fecha en feriado (' + r.decreto[3] + '): los servicios turísticos llevan IVA ' + r.decreto[2] + ' %; los demás, ' + r.general + ' %.'
                    : 'Fecha sin feriado con decreto: IVA ' + r.general + ' % para los servicios con tarifa general.';
            }
        }

        function preparar(f) {
            const sel = f.querySelector('[name="servicio_id"]');
            sel.addEventListener('change', function () {
                const op = sel.selectedOptions[0];
                if (op && op.value) {
                    const precio = f.querySelector('[name="precio"]');
                    if (!precio.value) precio.value = String(parseFloat(op.dataset.precio));
                    const desc = f.querySelector('[name="descripcion"]');
                    if (!desc.value) desc.value = op.dataset.nombre;
                    const cant = f.querySelector('[name="cantidad"]');
                    if (!cant.value) cant.value = '1';
                }
                recalcular();
            });
            f.querySelectorAll('input').forEach(function (i) { i.addEventListener('input', recalcular); });
            f.querySelector('[data-quitar]').addEventListener('click', function () {
                if (cuerpo.querySelectorAll('tr.linea').length <= 1) {
                    f.querySelectorAll('input').forEach(function (i) { i.value = ''; });
                    sel.value = ''; sel.dispatchEvent(new Event('change'));
                } else { f.remove(); recalcular(); }
            });
        }
        cuerpo.querySelectorAll('tr.linea').forEach(preparar);
        const agregar = form.querySelector('[data-agregar]');
        if (agregar) agregar.addEventListener('click', function () {
            const filas = cuerpo.querySelectorAll('tr.linea');
            const nueva = filas[filas.length - 1].cloneNode(true);
            nueva.querySelectorAll('input').forEach(function (i) { i.value = ''; });
            nueva.querySelector('[name="servicio_id"]').value = '';
            delete nueva.dataset.pct;
            cuerpo.appendChild(nueva);
            preparar(nueva);
            recalcular();
            setTimeout(function () { const c = nueva.querySelector('.buscable-campo, select'); if (c) c.focus(); }, 0);
        });
        campoFecha.addEventListener('change', recalcular);
        campoFecha.addEventListener('input', recalcular);

        const forma = form.querySelector('[name="forma_pago"]');
        const plazo = document.getElementById('campoPlazo');
        function verPlazo() { if (forma && plazo) plazo.hidden = forma.value !== 'credito'; }
        if (forma) forma.addEventListener('change', verPlazo);
        verPlazo();
        recalcular();
    }

    // -----------------------------------------------------------------
    //  Cobro: trae las facturas pendientes del cliente
    // -----------------------------------------------------------------
    const cobro = document.getElementById('formCobro');
    if (cobro) {
        const cliente = cobro.querySelector('[name="cliente_id"]');
        const tabla = cobro.querySelector('tbody');
        const total = cobro.querySelector('[data-total-cobro]');
        function sumar() {
            let t = 0;
            tabla.querySelectorAll('[name="valor"]').forEach(function (i) { t += centavos(i.value); });
            total.textContent = dinero(t);
        }
        function cargar() {
            tabla.innerHTML = '';
            if (!cliente.value) { tabla.innerHTML = '<tr><td colspan="5" class="vacio">Elige el cliente.</td></tr>'; sumar(); return; }
            fetch(cobro.dataset.pendientes + '?cliente=' + cliente.value, { credentials: 'same-origin' })
                .then(function (r) { return r.json(); }).then(function (d) {
                    if (!d.facturas.length) tabla.innerHTML = '<tr><td colspan="5" class="vacio">Este cliente no debe nada.</td></tr>';
                    d.facturas.forEach(function (f) {
                        const tr = document.createElement('tr');
                        [f.numero, f.fecha, f.vence, f.saldo].forEach(function (t, i) {
                            const td = document.createElement('td'); td.textContent = t; if (i === 3) td.className = 'num'; tr.appendChild(td);
                        });
                        const td = document.createElement('td');
                        td.innerHTML = '<input type="hidden" name="factura_id"><input name="valor" class="entrada num" inputmode="decimal">';
                        td.querySelector('[name="factura_id"]').value = f.id;
                        td.querySelector('[name="valor"]').value = f.saldo;
                        td.querySelector('[name="valor"]').addEventListener('input', sumar);
                        tr.appendChild(td);
                        tabla.appendChild(tr);
                    });
                    sumar();
                });
        }
        cliente.addEventListener('change', cargar);
        cargar();
    }
})();
