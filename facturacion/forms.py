"""Formularios con validación de cédula, RUC y fechas."""
from django import forms

from . import servicios
from .models import Cliente, DecretoFeriado, Empleado, Empresa, Servicio, TasaIva


class Estilo(forms.ModelForm):
    """Aplica el estilo de la aplicación a todos los campos."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            if isinstance(campo.widget, forms.CheckboxInput):
                continue
            campo.widget.attrs.setdefault('class', 'entrada')
            if isinstance(campo.widget, forms.DateInput):
                campo.widget.input_type = 'date'
                campo.widget.format = '%Y-%m-%d'


class ClienteForm(Estilo):
    class Meta:
        model = Cliente
        fields = ['tipo_identificacion', 'identificacion', 'nombre', 'correo', 'telefono', 'direccion', 'pais', 'activo']
        error_messages = {'identificacion': {'unique': 'Ya hay un cliente registrado con esa identificación.'}}

    def clean(self):
        datos = super().clean()
        ok, mensaje = servicios.revisar_identificacion(datos.get('tipo_identificacion'), datos.get('identificacion'))
        if not ok:
            self.add_error('identificacion', mensaje)
        return datos


class ServicioForm(Estilo):
    class Meta:
        model = Servicio
        fields = ['codigo', 'nombre', 'categoria', 'precio', 'tarifa_iva', 'turistico', 'activo']
        widgets = {'precio': forms.NumberInput(attrs={'step': '0.01', 'min': '0'})}
        error_messages = {'codigo': {'unique': 'Ya hay un servicio con ese código.'}}

    def clean_codigo(self):
        return self.cleaned_data['codigo'].strip().upper()

    def clean_precio(self):
        precio = self.cleaned_data['precio']
        if precio is not None and precio < 0:
            raise forms.ValidationError('El precio no puede ser negativo.')
        return precio


class DecretoForm(Estilo):
    class Meta:
        model = DecretoFeriado
        fields = ['feriado', 'decreto', 'desde', 'hasta', 'porcentaje', 'registro_oficial']
        widgets = {'desde': forms.DateInput(), 'hasta': forms.DateInput()}

    def clean(self):
        datos = super().clean()
        desde, hasta = datos.get('desde'), datos.get('hasta')
        if desde and hasta:
            if hasta < desde:
                self.add_error('hasta', 'La fecha final no puede ser anterior a la inicial.')
            cruce = DecretoFeriado.objects.filter(desde__lte=hasta, hasta__gte=desde)
            if self.instance.pk:
                cruce = cruce.exclude(pk=self.instance.pk)
            if cruce.exists():
                self.add_error('desde', f'Esas fechas se cruzan con el decreto de {cruce.first().feriado}.')
        pct = datos.get('porcentaje')
        if pct is not None and not (0 <= pct < 15):
            self.add_error('porcentaje', 'El IVA reducido debe estar entre 0 y 15 %.')
        return datos


class TasaForm(Estilo):
    class Meta:
        model = TasaIva
        fields = ['porcentaje', 'desde', 'base_legal']
        widgets = {'desde': forms.DateInput()}
        error_messages = {'desde': {'unique': 'Ya hay una tarifa registrada desde esa fecha.'}}

    def clean_porcentaje(self):
        p = self.cleaned_data['porcentaje']
        if p is not None and not (0 < p <= 30):
            raise forms.ValidationError('Escribe un porcentaje entre 0 y 30.')
        return p


class EmpresaForm(Estilo):
    class Meta:
        model = Empresa
        fields = ['ruc', 'razon_social', 'nombre_comercial', 'actividad', 'direccion', 'telefono', 'correo',
                  'establecimiento', 'punto_emision', 'regimen', 'obligado_contabilidad', 'contribuyente_especial',
                  'agente_retencion']

    def clean_ruc(self):
        ok, mensaje = servicios.revisar_ruc(self.cleaned_data['ruc'])
        if not ok:
            raise forms.ValidationError(mensaje)
        return self.cleaned_data['ruc']

    def clean(self):
        datos = super().clean()
        for campo in ('establecimiento', 'punto_emision'):
            v = (datos.get(campo) or '').strip()
            if not (len(v) == 3 and v.isdigit() and v != '000'):
                self.add_error(campo, 'Debe tener 3 números, por ejemplo 001.')
        return datos


class EmpleadoForm(Estilo):
    class Meta:
        model = Empleado
        fields = ['rol', 'activo']
