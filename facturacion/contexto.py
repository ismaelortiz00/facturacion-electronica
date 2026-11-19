from django.utils import timezone


def datos_comunes(request):
    """Empresa, empleado y el IVA que rige hoy: se muestran en la barra superior de todas las pantallas."""
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {}
    from . import servicios
    from .models import Empleado, Empresa, ErrorNegocio
    try:
        iva_hoy = servicios.estado_iva(timezone.localdate())
    except ErrorNegocio:
        iva_hoy = None
    return {'empresa': Empresa.actual(), 'empleado': Empleado.objects.filter(usuario=request.user).first(),
            'iva_hoy': iva_hoy}
