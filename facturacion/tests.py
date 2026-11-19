"""
Pruebas de la regla central (IVA por feriado) y del flujo de facturación.
Ejecutar: python manage.py test
"""
from datetime import date, timedelta
from decimal import Decimal as D

from django.test import RequestFactory, TestCase
from django.utils import timezone

from cuentas.models import Usuario
from cuentas.servicios import huella_navegador

from . import servicios as S
from .models import Cliente, Comprobante, DecretoFeriado, Empleado, Empresa, ErrorNegocio, Servicio

CARNAVAL = date(2026, 2, 15)      # dentro del Decreto 304
NORMAL = date(2026, 2, 20)        # día común


class Base(TestCase):
    def setUp(self):
        self.u = Usuario.objects.create_user('0903686962', 'ana@ik.ec', 'Ana Pérez', 'Turismo#2026', correo_verificado=True)
        Empleado.objects.create(usuario=self.u, rol='admin', activo=True)
        Empresa.objects.create(ruc='1792146739001', razon_social='Hostería Mindo Verde S.A.', direccion='Mindo')
        self.cliente = Cliente.objects.create(tipo_identificacion='cedula', identificacion='1710034065', nombre='Luis Mora',
                                              correo='luis@correo.ec')
        self.tour = Servicio.objects.create(codigo='TOUR', nombre='Tour Mindo', precio=D('100'), turistico=True)
        self.souvenir = Servicio.objects.create(codigo='SOUV', nombre='Camiseta', precio=D('20'), turistico=False)
        self.libro = Servicio.objects.create(codigo='GUIA', nombre='Guía impresa', precio=D('10'), tarifa_iva='0')

    def factura(self, fecha, lineas, emitir=True, **datos):
        c = S.guardar(self.u, {'fecha': fecha, 'cliente_id': self.cliente.id, **datos}, lineas)
        return S.emitir(c, self.u) if emitir else c

    def entrar(self):
        self.client.force_login(self.u)
        s = self.client.session
        s['huella'] = huella_navegador(RequestFactory().get('/'))
        s.save()


class ReglaIva(Base):
    def test_turistico_en_feriado_paga_8(self):
        f = self.factura(CARNAVAL, [{'servicio_id': self.tour.id, 'cantidad': 2}])
        l = f.lineas.get()
        self.assertEqual((l.porcentaje_iva, l.iva), (D('8'), D('16.00')))
        self.assertEqual(f.decreto.decreto, 'Decreto Ejecutivo 304')
        self.assertEqual((f.base_reducida, f.base_general, f.total), (D('200'), D('0'), D('216.00')))

    def test_turistico_fuera_de_feriado_paga_15(self):
        f = self.factura(NORMAL, [{'servicio_id': self.tour.id, 'cantidad': 2}])
        self.assertEqual(f.lineas.get().porcentaje_iva, D('15'))
        self.assertIsNone(f.decreto)
        self.assertEqual(f.total, D('230.00'))

    def test_no_turistico_en_feriado_sigue_en_15(self):
        f = self.factura(CARNAVAL, [{'servicio_id': self.tour.id}, {'servicio_id': self.souvenir.id},
                                    {'servicio_id': self.libro.id}])
        pcts = {l.servicio.codigo: l.porcentaje_iva for l in f.lineas.all()}
        self.assertEqual(pcts, {'TOUR': D('8'), 'SOUV': D('15'), 'GUIA': D('0')})
        self.assertEqual(f.iva, D('8.00') + D('3.00'))
        self.assertEqual((f.base_reducida, f.base_general, f.base_cero), (D('100'), D('20'), D('10')))

    def test_bordes_del_decreto(self):
        self.assertEqual(S.iva_para(self.tour, date(2026, 2, 14))[0], D('8'))   # primer día
        self.assertEqual(S.iva_para(self.tour, date(2026, 2, 17))[0], D('8'))   # último día
        self.assertEqual(S.iva_para(self.tour, date(2026, 2, 13))[0], D('15'))  # día antes
        self.assertEqual(S.iva_para(self.tour, date(2026, 2, 18))[0], D('15'))  # día después

    def test_tarifa_general_por_fecha(self):
        self.assertEqual(S.tasa_general(date(2024, 3, 31)), D('12'))
        self.assertEqual(S.tasa_general(date(2024, 4, 1)), D('15'))

    def test_decreto_nuevo_se_aplica_solo(self):
        navidad = date(2026, 12, 25)
        self.assertEqual(S.iva_para(self.tour, navidad)[0], D('15'))
        DecretoFeriado.objects.create(feriado='Navidad', decreto='Decreto de prueba', desde=date(2026, 12, 24),
                                      hasta=date(2026, 12, 27), porcentaje=D('8'))
        self.assertEqual(S.iva_para(self.tour, navidad)[0], D('8'))

    def test_iva_se_redondea_por_tarifa(self):
        barato = Servicio.objects.create(codigo='CAFE', nombre='Café', precio=D('0.03'), turistico=False)
        f = self.factura(NORMAL, [{'servicio_id': barato.id}] * 3)
        self.assertEqual(f.iva, D('0.01'))  # 0.09 × 15 % = 0.0135, no 3 × 0.00


class Facturas(Base):
    def test_numeracion_sin_saltos_y_sin_doble_emision(self):
        a = self.factura(NORMAL, [{'servicio_id': self.tour.id}])
        b = self.factura(NORMAL, [{'servicio_id': self.tour.id}])
        self.assertEqual((a.numero, b.numero), ('001-001-000000001', '001-001-000000002'))
        with self.assertRaises(ErrorNegocio):
            S.emitir(a, self.u)

    def test_consumidor_final_hasta_50(self):
        cf = Cliente.objects.create(tipo_identificacion='consumidor', identificacion='9999999999999', nombre='Consumidor final')
        c = S.guardar(self.u, {'fecha': NORMAL, 'cliente_id': cf.id}, [{'servicio_id': self.tour.id}])
        with self.assertRaises(ErrorNegocio):
            S.emitir(c, self.u)

    def test_nota_de_credito_usa_el_iva_de_la_factura(self):
        f = self.factura(CARNAVAL, [{'servicio_id': self.tour.id, 'cantidad': 3}], forma_pago='credito')
        nc = S.guardar(self.u, {'tipo': 'nota_credito', 'fecha': NORMAL, 'cliente_id': self.cliente.id, 'modifica_id': f.id,
                                'motivo': 'Canceló un tour'}, [{'servicio_id': self.tour.id, 'cantidad': 1}])
        nc = S.emitir(nc, self.u)
        self.assertEqual(nc.lineas.get().porcentaje_iva, D('8'))  # aunque el 20/02 ya no es feriado
        self.assertEqual(nc.total, D('108.00'))
        self.assertEqual(f.saldo(), D('324.00') - D('108.00'))

    def test_nota_de_credito_no_supera_la_factura(self):
        f = self.factura(NORMAL, [{'servicio_id': self.tour.id}])
        nc = S.guardar(self.u, {'tipo': 'nota_credito', 'fecha': NORMAL, 'cliente_id': self.cliente.id, 'modifica_id': f.id,
                                'motivo': 'Error de cobro'}, [{'servicio_id': self.tour.id, 'cantidad': 2}])
        with self.assertRaises(ErrorNegocio):
            S.emitir(nc, self.u)

    def test_anular_con_cobros_no_se_puede(self):
        f = self.factura(NORMAL, [{'servicio_id': self.tour.id}], forma_pago='credito', plazo_dias=15)
        self.assertEqual(f.vencimiento, NORMAL + timedelta(days=15))
        S.registrar_cobro(self.u, {'fecha': NORMAL, 'cliente_id': self.cliente.id}, [{'factura_id': f.id, 'valor': '50'}])
        with self.assertRaises(ErrorNegocio):
            S.anular(f, self.u, 'Cliente equivocado')

    def test_corregir_deja_copia_en_borrador(self):
        f = self.factura(NORMAL, [{'servicio_id': self.tour.id, 'cantidad': 2}])
        copia = S.corregir(f, self.u, 'Cantidad equivocada')
        f.refresh_from_db()
        self.assertEqual((f.estado, copia.estado), ('anulado', 'borrador'))
        self.assertEqual(copia.lineas.get().cantidad, D('2'))


class Cobros(Base):
    def test_cobro_parcial_y_total(self):
        f = self.factura(NORMAL, [{'servicio_id': self.tour.id}], forma_pago='credito')
        S.registrar_cobro(self.u, {'fecha': NORMAL, 'cliente_id': self.cliente.id}, [{'factura_id': f.id, 'valor': '15'}])
        self.assertEqual(f.saldo(), D('100.00'))
        with self.assertRaises(ErrorNegocio):
            S.registrar_cobro(self.u, {'fecha': NORMAL, 'cliente_id': self.cliente.id}, [{'factura_id': f.id, 'valor': '101'}])
        c = S.registrar_cobro(self.u, {'fecha': NORMAL, 'cliente_id': self.cliente.id}, [{'factura_id': f.id, 'valor': '100'}])
        self.assertEqual(f.saldo(), 0)
        S.anular_cobro(c, self.u, 'Transferencia rebotada')
        self.assertEqual(f.saldo(), D('100.00'))

    def test_cartera_por_antiguedad(self):
        f = self.factura(date(2026, 1, 10), [{'servicio_id': self.tour.id}], forma_pago='credito', plazo_dias=30)
        c = S.cartera(date(2026, 3, 1))  # vence 09/02 → 20 días de atraso
        self.assertEqual(c['totales']['d30'], f.total)


class Reportes(Base):
    def test_resumen_separa_feriado_y_resta_notas(self):
        self.factura(CARNAVAL, [{'servicio_id': self.tour.id}])
        f = self.factura(NORMAL, [{'servicio_id': self.tour.id}, {'servicio_id': self.souvenir.id}])
        nc = S.guardar(self.u, {'tipo': 'nota_credito', 'fecha': NORMAL, 'cliente_id': self.cliente.id, 'modifica_id': f.id,
                                'motivo': 'Devolución'}, [{'servicio_id': self.souvenir.id}])
        S.emitir(nc, self.u)
        r = S.resumen_ventas(date(2026, 2, 1), date(2026, 2, 28))
        self.assertEqual(r['neto']['reducida'], D('100'))
        self.assertEqual(r['neto']['general'], D('100'))
        self.assertEqual(r['iva_reducida'], D('8.00'))
        self.assertEqual(r['iva_general'], D('15.00'))
        self.assertEqual(r['ahorro_clientes'], D('7.00'))  # 100 × (15 % − 8 %)


class Validaciones(TestCase):
    def test_identificaciones(self):
        self.assertTrue(S.revisar_identificacion('cedula', '1710034065')[0])
        self.assertFalse(S.revisar_identificacion('cedula', '1710034066')[0])
        self.assertTrue(S.revisar_identificacion('ruc', '1790016919001')[0])
        self.assertFalse(S.revisar_identificacion('ruc', '1790016919000')[0])
        self.assertTrue(S.revisar_identificacion('pasaporte', 'X4587921')[0])


class Pantallas(Base):
    def test_todas_abren(self):
        self.entrar()
        f = self.factura(CARNAVAL, [{'servicio_id': self.tour.id}], forma_pago='credito')
        for url in ['/app/', '/app/clientes/', '/app/clientes/nuevo/', '/app/servicios/', '/app/servicios/nuevo/',
                    '/app/feriados/', '/app/facturas/', '/app/facturas/nueva/', '/app/facturas/nueva/?tipo=nota_credito&modifica=%d' % f.id,
                    f'/app/facturas/{f.id}/', '/app/cobros/', '/app/cobros/nuevo/', f'/app/cobros/pendientes/?cliente={self.cliente.id}',
                    '/app/reportes/ventas/', '/app/reportes/ventas/?formato=excel', '/app/reportes/iva/', '/app/reportes/cartera/',
                    '/app/configuracion/empresa/', '/app/configuracion/usuarios/', '/app/configuracion/bitacora/',
                    '/app/facturas/calcular/?fecha=2026-02-15', '/sri/', '/sri/firma/', '/asistente/']:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_editar_cliente_muestra_sus_datos(self):
        self.entrar()
        html = self.client.get(f'/app/clientes/{self.cliente.id}/').content.decode()
        self.assertIn('value="1710034065"', html)
        self.assertIn('value="luis@correo.ec"', html)

    def test_factura_con_error_conserva_lo_escrito(self):
        self.entrar()
        r = self.client.post('/app/facturas/nueva/', {
            'tipo': 'factura', 'fecha': '2026-02-15', 'cliente_id': '', 'forma_pago': 'transferencia', 'accion': 'emitir',
            'observacion': 'Reserva 77', 'servicio_id': [self.tour.id], 'descripcion': ['Tour especial'], 'cantidad': ['3'],
            'precio': ['90'], 'descuento': ['']})
        html = r.content.decode()
        self.assertIn('Elige el cliente', html)
        self.assertIn('value="Reserva 77"', html)
        self.assertIn('value="Tour especial"', html)
        self.assertIn('value="3"', html)
        self.assertIn('value="2026-02-15"', html)
        self.assertFalse(Comprobante.objects.exists())

    def test_cliente_nuevo_desde_factura_vuelve_elegido(self):
        self.entrar()
        r = self.client.post('/app/clientes/nuevo/?volver=/app/facturas/nueva/', {
            'tipo_identificacion': 'pasaporte', 'identificacion': 'AB12345', 'nombre': 'Emma', 'pais': 'Alemania', 'activo': 'on'})
        nuevo = Cliente.objects.get(identificacion='AB12345')
        self.assertEqual(r['Location'], f'/app/facturas/nueva/?cliente={nuevo.id}')
        r = self.client.post('/app/clientes/nuevo/?volver=https://sitio-malo.com/', {
            'tipo_identificacion': 'pasaporte', 'identificacion': 'CD12345', 'nombre': 'Otro', 'pais': 'X', 'activo': 'on'})
        self.assertTrue(r['Location'].startswith('/app/clientes/'))

    def test_nota_de_credito_sobre_factura_pagada(self):
        from facturacion import servicios as sv
        f = self.factura(date(2026, 3, 10), [{'servicio_id': self.tour.id}], forma_pago='credito')
        sv.registrar_cobro(self.u, {'fecha': date(2026, 3, 10), 'cliente_id': self.cliente.id, 'forma': 'efectivo'},
                           [{'factura_id': f.id, 'valor': f.total}])
        self.assertEqual(f.saldo(), 0)
        nc = sv.guardar(self.u, {'tipo': 'nota_credito', 'fecha': date(2026, 3, 11), 'cliente_id': self.cliente.id,
                                 'modifica_id': f.id, 'motivo': 'Devolución por lluvia'},
                        [{'servicio_id': self.tour.id, 'cantidad': '1', 'precio': '100'}])
        sv.emitir(nc, self.u)
        self.assertEqual(f.saldo(), 0)
        otra = sv.guardar(self.u, {'tipo': 'nota_credito', 'fecha': date(2026, 3, 11), 'cliente_id': self.cliente.id,
                                   'modifica_id': f.id, 'motivo': 'Devolución repetida'},
                          [{'servicio_id': self.tour.id, 'cantidad': '1', 'precio': '100'}])
        with self.assertRaises(ErrorNegocio):
            sv.emitir(otra, self.u)

    def test_ids_basura_no_rompen_el_servidor(self):
        from facturacion import servicios as sv
        for malo in ('abc', '-1', '1; DROP', None, ''):
            with self.assertRaises(ErrorNegocio):
                sv.guardar(self.u, {'tipo': 'factura', 'cliente_id': malo}, [{'servicio_id': 'x', 'cantidad': '1', 'precio': '1'}])
            with self.assertRaises(ErrorNegocio):
                sv.registrar_cobro(self.u, {'cliente_id': malo, 'forma': 'efectivo'}, [{'factura_id': malo, 'valor': '5'}])

    def test_factura_desde_el_formulario(self):
        self.entrar()
        r = self.client.post('/app/facturas/nueva/', {
            'tipo': 'factura', 'fecha': '2026-02-15', 'cliente_id': self.cliente.id, 'forma_pago': 'efectivo',
            'accion': 'emitir', 'servicio_id': [self.tour.id, self.souvenir.id], 'descripcion': ['', ''],
            'cantidad': ['2', '1'], 'precio': ['100', '20'], 'descuento': ['', '']})
        f = Comprobante.objects.get()
        self.assertRedirects(r, f'/app/facturas/{f.id}/', fetch_redirect_response=False)
        self.assertEqual((f.estado, f.total), ('emitido', D('216.00') + D('23.00')))

    def test_vendedor_sin_aprobar_no_entra_y_no_anula(self):
        otro = Usuario.objects.create_user('1710034065', 'v@ik.ec', 'Vendedor', 'Turismo#2026', correo_verificado=True)
        self.client.force_login(otro)
        s = self.client.session
        s['huella'] = huella_navegador(RequestFactory().get('/'))
        s.save()
        self.assertEqual(self.client.get('/app/').status_code, 403)
        Empleado.objects.filter(usuario=otro).update(activo=True)
        f = self.factura(NORMAL, [{'servicio_id': self.tour.id}])
        self.client.post(f'/app/facturas/{f.id}/accion/', {'accion': 'anular', 'motivo': 'Prueba de permisos'})
        f.refresh_from_db()
        self.assertEqual(f.estado, 'emitido')
        self.assertEqual(self.client.get('/app/configuracion/usuarios/').status_code, 302)
