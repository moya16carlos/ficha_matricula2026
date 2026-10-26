"""Vistas de autenticación y home (login, home, logout, error 403)."""
from ._common import *  # noqa: F401,F403


def login_view(request):
    # Ya autenticado → directo al dashboard
    if request.user.is_authenticated:
        return redirect('home')
    # Obtén la única fila (si no existe, crea una con valor por defecto)
    cfg, _ = ConfiguracionSistema.objects.get_or_create(
        id=1, defaults={'registro_apoderado_habilitado': False}
    )
    registro_habilitado = bool(cfg.registro_apoderado_habilitado)

    if request.method == 'POST':
        run = (request.POST.get('run') or '').strip()
        password = request.POST.get('password') or ''
        user = authenticate(request, username=run, password=password)
        if user is not None:
            login(request, user)
            return redirect('home')
        messages.error(request, 'Usuario o contraseña incorrectos')

    return render(request, 'app/login.html', {
        'registro_habilitado': registro_habilitado
    })

@login_required
def home_view(request):
    """Inicio-dashboard con cifras reales del sistema (año en curso)."""
    from .estudiantes import _es_rut_provisional

    anio_actual = date.today().year
    estudiantes = Estudiante.objects.filter(
        filtro_q_por_niveles_permitidos(request.user, 'curso__nivel')
    )
    ruts_prov = [r for r in estudiantes.values_list('rut', flat=True) if _es_rut_provisional(r)]
    roles_resumen = (Usuarios.objects.values('rol')
                     .annotate(n=Count('id_usuario')).order_by('-n'))
    ctx = {
        'anio_actual': anio_actual,
        'n_estudiantes': estudiantes.count(),
        'ultimas_fichas': estudiantes.select_related('curso').order_by('-id')[:4],
        'n_matriculas_anio': MatriculaAnual.objects.filter(anio=anio_actual).filter(
            filtro_q_por_niveles_permitidos(request.user, 'curso__nivel')
        ).count(),
        'n_provisionales': len(ruts_prov),
        'n_cursos_anio': (Curso.objects.filter(anio=anio_actual)
                          .exclude(nivel=NIVEL_POR_ASIGNAR)
                          .exclude(nivel=NIVEL_EGRESADOS).count()),
        'n_usuarios': Usuarios.objects.count(),
        'roles_resumen': roles_resumen,
        'cola_prov': (Estudiante.objects.filter(rut__in=ruts_prov)
                      .select_related('curso').order_by('nombre_completo')[:4]),
    }
    return render(request, 'app/home.html', ctx)

# Vista de logout
@login_required
def logout_view(request):
    logout(request)
    return redirect('login')

def error_403_view(request, exception=None):
    """
    Muestra una página de error elegante cuando el usuario intenta
    acceder a una vista sin permisos.
    """
    response = render(request, '403.html', status=403)
    response.status_code = 403
    return response

