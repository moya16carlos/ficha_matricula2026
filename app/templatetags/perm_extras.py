# app/perm_extras.py
from django import template
from app.roles import BASICA, MEDIA, curso_nivel_to_group, has_perm_on_nivel

register = template.Library()


@register.simple_tag
def puede_matricular(user):
    return any(has_perm_on_nivel(user, 'add', nivel) for nivel in (BASICA, MEDIA))


@register.simple_tag
def permiso_curso(user, curso, accion):
    nivel = curso_nivel_to_group(curso.nivel) if curso else None
    return all(has_perm_on_nivel(user, accion, nv) for nv in ((nivel,) if nivel else (BASICA, MEDIA)))

@register.filter
def get_perm(qs, nivel):
    try:
        return qs.get(nivel=nivel)
    except:
        return None
