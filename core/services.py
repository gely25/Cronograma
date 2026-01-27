import pandas as pd
from datetime import datetime, timedelta, time
from django.db import transaction
from .models import Responsable, Equipo, Turno, Feriado, ConfiguracionCronograma

def procesar_archivo_activos(archivo):
    """
    Procesa el archivo Excel cargado, crea Responsables y Equipos.
    Limpia los datos existentes para evitar duplicados.
    """
    df = pd.read_excel(archivo)

    # Normalizar nombres de columnas (MAYÚSCULAS y cambiar espacios por _)
    df.columns = [str(c).strip().upper().replace(' ', '_') for c in df.columns]
    
    # Columnas esperadas: RESPONSABLE, CPDOGP_GOBIERNO, MARCA, MODELO, DESCRIPCION

    with transaction.atomic():
        # Limpiar datos existentes para evitar duplicados
        print(f"Limpiando datos anteriores...")
        Turno.objects.all().delete()
        Equipo.objects.all().delete()
        Responsable.objects.all().delete()
        
        print(f"Procesando {len(df)} filas del Excel...")
        
        for index, row in df.iterrows():
            nombre_responsable = str(row.get('RESPONSABLE', '')).strip()
            if not nombre_responsable or nombre_responsable.lower() in ['nan', 'nat', '']:
                continue

            responsable, created = Responsable.objects.get_or_create(nombre=nombre_responsable)
            
            # Crear equipo asociado
            Equipo.objects.create(
                responsable=responsable,
                codigo=row.get('CPDOGP_GOBIERNO'),
                marca=row.get('MARCA'),
                modelo=row.get('MODELO'),
                descripcion=row.get('DESCRIPCION')
            )
            
            # Asegurar que el responsable tenga un turno (estado pendiente por defecto)
            Turno.objects.get_or_create(responsable=responsable)
        
        total_responsables = Responsable.objects.count()
        total_equipos = Equipo.objects.count()
        total_turnos = Turno.objects.count()
        print(f"✅ Datos procesados: {total_responsables} responsables, {total_equipos} equipos, {total_turnos} turnos")

def generar_slots(config):
    """
    Genera todos los slots disponibles basados en la configuración.
    """
    slots = []
    feriados = set(Feriado.objects.values_list('fecha', flat=True))
    
    fecha_actual = config.fecha_inicio
    while fecha_actual <= config.fecha_fin:
        # Lógica de exclusión granular
        es_sabado = fecha_actual.weekday() == 5
        es_domingo = fecha_actual.weekday() == 6
        
        excluir = False
        if config.modo_exclusion == 'weekends':
            if es_sabado or es_domingo: excluir = True
        elif config.modo_exclusion == 'sundays':
            if es_domingo: excluir = True
            
        if not excluir and fecha_actual not in feriados:
            
            # Hora de inicio y fin como datetime para iterar
            curr_dt = datetime.combine(fecha_actual, config.hora_inicio)
            end_dt = datetime.combine(fecha_actual, config.hora_fin)
            
            # Almuerzo
            lunch_start = datetime.combine(fecha_actual, config.hora_almuerzo)
            lunch_end = lunch_start + timedelta(minutes=config.duracion_almuerzo)
            
            while curr_dt + timedelta(minutes=config.duracion_turno) <= end_dt:
                slot_end = curr_dt + timedelta(minutes=config.duracion_turno)
                
                # Validar si el slot cae en almuerzo
                # Un slot cae en almuerzo si empieza antes del fin del almuerzo Y termina después del inicio del almuerzo
                if not (curr_dt < lunch_end and slot_end > lunch_start):
                    slots.append({
                        'fecha': fecha_actual,
                        'hora': curr_dt.time()
                    })
                
                curr_dt += timedelta(minutes=config.duracion_turno)
                
        fecha_actual += timedelta(days=1)
    
    return slots

def asignar_turnos_automatico():
    """
    Asigna turnos automáticamente a todos los responsables pendientes.
    """
    config = ConfiguracionCronograma.objects.last()
    if not config:
        return 0, "No hay configuración de cronograma. Por favor, guarda la configuración primero."
    
    # Validar que la configuración tenga fechas
    if not config.fecha_inicio or not config.fecha_fin:
        return 0, "La configuración debe incluir fechas de inicio y fin."

    slots = generar_slots(config)
    # Ordenar por ID del responsable para mantener el orden del Excel
    turnos_pendientes = Turno.objects.filter(estado='pendiente').order_by('responsable__id')
    
    total_responsables = Responsable.objects.count()
    total_turnos = Turno.objects.count()
    
    print(f"Estadísticas:")
    print(f"  - Total responsables únicos: {total_responsables}")
    print(f"  - Total turnos en BD: {total_turnos}")
    print(f"  - Turnos pendientes: {turnos_pendientes.count()}")
    print(f"  - Slots disponibles: {len(slots)}")
    print(f"  - Período: {config.fecha_inicio} a {config.fecha_fin}")
    
    if len(slots) < turnos_pendientes.count():
        dias_laborables = len(set([s['fecha'] for s in slots]))
        return 0, {
            'error_type': 'insufficient_slots',
            'slots_generados': len(slots),
            'dias_laborables': dias_laborables,
            'usuarios_pendientes': turnos_pendientes.count(),
            'faltantes': turnos_pendientes.count() - len(slots),
            'sugerencias': [
                "Amplía el rango de fechas en la configuración.",
                "Reduce la duración de cada turno.",
                "Verifica que no haya demasiados feriados configurados.",
                "Aumenta la jornada laboral diaria."
            ]
        }

    turnos_actualizados = []
    with transaction.atomic():
        for i, turno in enumerate(turnos_pendientes):
            if i < len(slots):
                turno.fecha = slots[i]['fecha']
                turno.hora = slots[i]['hora']
                turno.estado = 'asignado'
                turnos_actualizados.append(turno)
        
        Turno.objects.bulk_update(turnos_actualizados, ['fecha', 'hora', 'estado'])
    
    return len(turnos_actualizados), f"Cronograma generado: {len(turnos_actualizados)} turnos asignados correctamente."
