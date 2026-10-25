# app/perm_extras.py
from django import template

register = template.Library()

@register.filter
def get_perm(qs, nivel):
    try:
        return qs.get(nivel=nivel)
    except:
        return None
