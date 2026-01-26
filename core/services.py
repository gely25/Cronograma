import pandas as pd
from datetime import datetime, timedelta, time
from django.db import transaction
from .models import Responsable, Dispositivo, Turno

def procesar_archivo_activos(archivo):
    """
    Procesa el archivo Excel cargado, crea Responsables y Dispositivos.
    """
    # Leer el archivo excel detectando tipos
    df = pd.read_excel(archivo)

    # Normalizar nombres de columnas
    df.columns = [str(c).strip().upper() for c in df.columns]
    
    # Columnas esperadas: RESPONSABLE, CÓDIGO INTERNO, CPDOGP GOBIERNO, FECHA INGRESO, DESCRIPCIÓN, MODELO, MARCA

    with transaction.atomic():
        # Nota: En una implementación real más compleja, podríamos querer verificar duplicados
        # de dispositivos para no reinsertarlos, aquí asumimos carga limpia o aditiva.
        
        for index, row in df.iterrows():
            nombre_responsable = str(row.get('RESPONSABLE', '')).strip()
            if not nombre_responsable or nombre_responsable.lower() in ['nan', 'nat']:
                continue

            responsable, created = Responsable.objects.get_or_create(nombre=nombre_responsable)
            
            # Crear dispositivo asociado
            Dispositivo.objects.create(
                responsable=responsable,
                codigo_interno=row.get('CÓDIGO INTERNO'),
                cpdogp_gobierno=row.get('CPDOGP GOBIERNO'),
                fecha_ingreso=str(row.get('FECHA INGRESO')) if pd.notna(row.get('FECHA INGRESO')) else None,
                descripcion=row.get('DESCRIPCIÓN'),
                modelo=row.get('MODELO'),
                marca=row.get('MARCA')
            )

def generar_cronograma(anio=2026, mes=2, dia_inicio=2):
    """
    Genera el cronograma distribuyendo responsables en 3 estaciones (A, B, C)
    por cada franja de 30 minutos.
    """
    Turno.objects.all().delete()
    responsables = Responsable.objects.all().order_by('nombre')
    
    fecha_actual = datetime(anio, mes, dia_inicio).date()
    hora_inicio_jornada = time(8, 0)
    hora_fin_jornada = time(17, 0)
    duracion_turno = timedelta(minutes=30)
    
    tiempo_actual = datetime.combine(fecha_actual, hora_inicio_jornada)
    estacion_actual = 1 # 1=A, 2=B, 3=C
    
    turnos_creados = []

    for responsable in responsables:
        # Si ya pasamos la hora de cierre, resetear al día siguiente
        if tiempo_actual.time() >= hora_fin_jornada:
            tiempo_actual += timedelta(days=1)
            tiempo_actual = tiempo_actual.replace(hour=8, minute=0)
            estacion_actual = 1

        # Crear turno
        turno = Turno(
            responsable=responsable,
            fecha=tiempo_actual.date(),
            hora_inicio=tiempo_actual.time(),
            hora_fin=(tiempo_actual + duracion_turno).time(),
            estacion=estacion_actual
        )
        turnos_creados.append(turno)
        
        # Lógica de distribución:
        # Pasamos a la siguiente estación en la misma hora
        estacion_actual += 1
        
        # Si llenamos las 3 estaciones, avanzamos 30 min y reseteamos estación a 1
        if estacion_actual > 3:
            estacion_actual = 1
            tiempo_actual += duracion_turno
            
            # Si al avanzar el tiempo nos pasamos de las 17:00, saltamos de día
            if tiempo_actual.time() >= hora_fin_jornada:
                tiempo_actual += timedelta(days=1)
                tiempo_actual = tiempo_actual.replace(hour=8, minute=0)

    Turno.objects.bulk_create(turnos_creados)
    return len(turnos_creados)
