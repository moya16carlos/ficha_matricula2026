import os
import django
import csv
from datetime import datetime
from app.models import Estudiante  # Asegúrate de que la ruta al modelo sea correcta

# Configuración del entorno de Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'matriculas_lsp.settings')  # Cambia 'matriculas_lsp' según tu proyecto
django.setup()

# Ruta al archivo CSV
CSV_FILE_PATH = r'C:\Users\soloo\Desktop\base_datos_completa.csv'

def convertir_a_booleano(valor):
    """Convierte el valor 'Sí' a True y 'No' a False, manejando variaciones."""
    if not valor:
        return False
    valor = valor.strip().lower().replace(".", "")  # Quitar espacios y puntos finales
    return valor == 'sí'

def convertir_fecha(fecha):
    """Convierte la fecha al formato ISO (YYYY-MM-DD) o devuelve None si es inválida."""
    try:
        return datetime.strptime(fecha.strip(), "%d/%m/%Y").date()  # Formato DD/MM/YYYY
    except ValueError:
        print(f"Fecha inválida: {fecha}")
        return None
def corregir_caracteres(texto):
    """Reemplaza caracteres incorrectos por los correctos en los textos importados."""
    reemplazos = {
        "Ã±": "ñ",
        "Ã©": "é",
        "Ã¡": "á",
        "Ã³": "ó",
        "Ãº": "ú",
        "Ã­": "í",
        "Ã�": "Í",
        "â€œ": "“",
        "â€": "”",
        "â€˜": "‘",
        "â€™": "’",
        "â€“": "–",
        "Ã": "Á",
        "Ã‰": "É",
        "Ã“": "Ó",
        "Ãš": "Ú",
    }
    for key, value in reemplazos.items():
        texto = texto.replace(key, value)
    return texto

def convertir_curso(curso):
    """Convierte nombres de cursos a un formato estándar."""
    curso = curso.strip().lower()
    conversion = {
        'prekinder.': 'Prekinder',
        'kã­nder.': 'Kinder',
        'primero.': '1° Básico',
        'segundo.': '2° Básico',
        'tercero.': '3° Básico',
        'cuarto.': '4° Básico',
        'quinto.': '5° Básico',
        'sexto.': '6° Básico',
        'séptimo.': '7° Básico',
        'sã©ptimo.': '7° Básico',  # Alternativa para evitar errores de acentuación
        'octavo.': '8° Básico',
        'primero medio.': '1° Medio',
        'segundo medio.': '2° Medio',
        'tercero medio.': '3° Medio',
        'cuarto medio.': '4° Medio'
    }
    # Buscar la conversión
    if curso in conversion:
        return conversion[curso]
    # Si no se encontró, devolvemos el valor original para revisar más tarde
    print(f"Curso desconocido: '{curso}' - Por favor revisa este valor.")
    return curso


def importar_estudiantes():
    try:
        with open(CSV_FILE_PATH, newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile, delimiter=';')

            # Verificar encabezados
            print("Encabezados del CSV:", reader.fieldnames)

            for index, row in enumerate(reader):
                try:
                    # Validar y convertir fecha de nacimiento
                    fecha_nacimiento = convertir_fecha(row['fecha_nacimiento'])
                    if not fecha_nacimiento:
                        print(f"Advertencia: Fecha de nacimiento inválida en la fila {index + 1}. Fila omitida.")
                        continue

                    curso_convertido = corregir_caracteres(convertir_curso(row['curso']))

                    # Limpieza y asignación de datos con corrección de caracteres
                    estudiante_data = {
                        'rut': corregir_caracteres(row['rut'].strip()),
                        'nombre_completo': corregir_caracteres(row['nombre_completo'].strip()),
                        'fecha_nacimiento': fecha_nacimiento,
                        'sexo': corregir_caracteres(row['sexo'].strip()),
                        'curso': corregir_caracteres(curso_convertido),
                        'nacionalidad': corregir_caracteres(row['nacionalidad'].strip()),
                        'domicilio': corregir_caracteres(row['domicilio'].strip()),
                        'comuna': corregir_caracteres(row['comuna'].strip()),
                        'ascendencia_indigena': convertir_a_booleano(row['ascendencia_indigena']),
                        'etnia': corregir_caracteres(row['etnia'].strip()) if convertir_a_booleano(row['ascendencia_indigena']) else None,
                        'pertenece_al_pie': convertir_a_booleano(row['pertenece_al_pie']),
                        'ha_repetido_curso': convertir_a_booleano(row['ha_repetido_curso']),
                        'estado': corregir_caracteres(row['estado'].strip()),
                        'nombre_madre': corregir_caracteres(row['nombre_madre'].strip()),
                        'rut_madre': corregir_caracteres(row['rut_madre'].strip()),
                        'nivel_educacional_madre': corregir_caracteres(row['nivel_educacional_madre'].strip()),
                        'ocupacion_madre': corregir_caracteres(row['ocupacion_madre'].strip()),
                        'telefono_madre': corregir_caracteres(row['telefono_madre'].strip()),
                        'email_madre': corregir_caracteres(row['email_madre'].strip()),
                        'nombre_padre': corregir_caracteres(row['nombre_padre'].strip()),
                        'rut_padre': corregir_caracteres(row['rut_padre'].strip()),
                        'nivel_educacional_padre': corregir_caracteres(row['nivel_educacional_padre'].strip()),
                        'ocupacion_padre': corregir_caracteres(row['ocupacion_padre'].strip()),
                        'telefono_padre': corregir_caracteres(row['telefono_padre'].strip()),
                        'email_padre': corregir_caracteres(row['email_padre'].strip()),
                        'nombre_apoderado': corregir_caracteres(row['nombre_apoderado'].strip()),
                        'rut_apoderado': corregir_caracteres(row['rut_apoderado'].strip()),
                        'nivel_educacional_apoderado': corregir_caracteres(row['nivel_educacional_apoderado'].strip()),
                        'ocupacion_apoderado': corregir_caracteres(row['ocupacion_apoderado'].strip()),
                        'telefono_apoderado': corregir_caracteres(row['telefono_apoderado'].strip()),
                        'email_apoderado': corregir_caracteres(row['email_apoderado'].strip()),
                        'nombre_apoderado_suplente': corregir_caracteres(row['nombre_apoderado_suplente'].strip()),
                        'rut_apoderado_suplente': corregir_caracteres(row['rut_apoderado_suplente'].strip()),
                        'nivel_educacional_apoderado_suplente': corregir_caracteres(row['nivel_educacional_apoderado_suplente'].strip()),
                        'ocupacion_apoderado_suplente': corregir_caracteres(row['ocupacion_apoderado_suplente'].strip()),
                        'telefono_apoderado_suplente': corregir_caracteres(row['telefono_apoderado_suplente'].strip()),
                        'vive_con': corregir_caracteres(row['vive_con'].strip()),
                        'resolucion_judicial': convertir_a_booleano(row['resolucion_judicial']),
                        'problemas_salud': convertir_a_booleano(row['problemas_salud']),
                        'descripcion_problemas_salud': corregir_caracteres(row['descripcion_problemas_salud'].strip()),
                        'impide_educacion_fisica': convertir_a_booleano(row['impide_educacion_fisica']),
                        'tipo_transporte': corregir_caracteres(row['tipo_transporte'].strip()),
                        'opta_religion': convertir_a_booleano(row['opta_religion']),
                        'certificado_medico': convertir_a_booleano(row['certificado_medico']),
                        'acepta_fotografias': convertir_a_booleano(row['acepta_fotografias']),
                    }

                    # Crear o actualizar el objeto Estudiante
                    estudiante, created = Estudiante.objects.update_or_create(
                        rut=estudiante_data['rut'],
                        defaults=estudiante_data
                    )

                    if created:
                        print(f"Estudiante {estudiante.nombre_completo} creado.")
                    else:
                        print(f"Estudiante {estudiante.nombre_completo} actualizado.")

                except KeyError as e:
                    print(f"Error: La clave '{e.args[0]}' no existe en la fila {index + 1}")
                except Exception as e:
                    print(f"Error al procesar la fila {index + 1}: {e}")

    except Exception as e:
        print(f"Ocurrió un error al importar los estudiantes: {e}")

# Ejecutar la función si se ejecuta este archivo directamente
if __name__ == "__main__":
    importar_estudiantes()
