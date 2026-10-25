# def curso_to_nivel_str(nivel_texto):
#     texto = (nivel_texto or '').lower()
#     # Lo que consideres “básica”
#     if any(x in texto for x in ['prekinder', 'pre-kinder', 'kinder', 'básico', 'basico']):
#         return 'BASICA'
#     if 'medio' in texto:
#         return 'MEDIA'
#     # fallback conservador (sin permiso = no pasa)
#     return None