import csv

# Archivo de entrada y salida
input_file = r'C:\Users\soloo\Desktop\base de datos limpia.csv'
output_cleaned_file = r'C:\Users\soloo\Desktop\base_de_datos_corregida.csv'
problematic_rows_file = r'C:\Users\soloo\Desktop\filas_problematicas.csv'

# Listas para almacenar filas válidas y problemáticas
filas_validas = []
filas_problematicas = []

with open(input_file, 'r', encoding='utf-8', errors='replace') as csvfile:
    reader = csv.reader(csvfile, delimiter=';')
    headers = next(reader)  # Guardar los encabezados para ambos archivos
    filas_validas.append(headers)

    for i, row in enumerate(reader, start=2):
        if '�' in str(row):
            # La fila tiene caracteres problemáticos (reemplazados por �)
            filas_problematicas.append(row)
            print(f"Fila {i} tiene caracteres problemáticos: {row}")
        else:
            # La fila es válida
            filas_validas.append(row)

# Guardar las filas válidas en un archivo limpio
with open(output_cleaned_file, 'w', newline='', encoding='utf-8') as outfile:
    writer = csv.writer(outfile, delimiter=';')
    writer.writerows(filas_validas)

# Guardar las filas problemáticas para revisión
with open(problematic_rows_file, 'w', newline='', encoding='utf-8') as prob_file:
    writer = csv.writer(prob_file, delimiter=';')
    writer.writerow(headers)  # Escribir encabezados
    writer.writerows(filas_problematicas)

print(f"Archivo limpio guardado como: {output_cleaned_file}")
print(f"Filas problemáticas guardadas como: {problematic_rows_file}")