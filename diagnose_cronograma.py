import os
import django
import sys

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gestion_activos.settings')
django.setup()

from core.models import ConfiguracionCronograma, Turno, Responsable, Feriado
from core.services import generar_slots

def diagnose():
    config = ConfiguracionCronograma.objects.last()
    if not config:
        print("DIAGNOSTIC: No configuration found.")
        return

    print(f"DIAGNOSTIC: Config found.")
    print(f"  - Period: {config.fecha_inicio} to {config.fecha_fin}")
    print(f"  - Working hours: {config.hora_inicio} to {config.hora_fin}")
    print(f"  - Turn duration: {config.duracion_turno} min")
    print(f"  - Lunch: {config.hora_almuerzo} ({config.duracion_almuerzo} min)")
    print(f"  - Exclusion: {config.modo_exclusion}")

    total_resp = Responsable.objects.count()
    pendientes = Turno.objects.filter(estado='pendiente').count()
    asignados = Turno.objects.filter(estado='asignado').count()
    proceso = Turno.objects.filter(estado='en_proceso').count()
    completados = Turno.objects.filter(estado='completado').count()
    
    print(f"DIAGNOSTIC: Data")
    print(f"  - Total Responsables: {total_resp}")
    print(f"  - Turnos Pendientes: {pendientes}")
    print(f"  - Turnos Asignados: {asignados}")
    print(f"  - Turnos En Proceso: {proceso}")
    print(f"  - Turnos Completados: {completados}")

    if config.fecha_inicio and config.fecha_fin:
        slots = generar_slots(config)
        print(f"DIAGNOSTIC: Slots")
        print(f"  - Slots generated: {len(slots)}")
        if len(slots) < pendientes:
            print(f"  - PROBLEM: Insufficient slots! Need {pendientes}, have {len(slots)}.")
        else:
            print(f"  - Slots are sufficient.")
    else:
        print("DIAGNOSTIC: Config missing dates.")

if __name__ == "__main__":
    diagnose()
