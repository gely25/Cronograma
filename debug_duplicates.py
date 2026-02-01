
import os
import django
import sys

# Setup Django
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gestion_activos.settings')
django.setup()

from core.models import Turno, Responsable, Equipo

def debug_duplicates():
    print("--- DIAGNÓSTICO DE DUPLICADOS Y EQUIPOS ---")
    
    # Buscar responsables con más de un turno
    counts = Responsable.objects.annotate(num_turnos=django.db.models.Count('turnos')).filter(num_turnos__gt=1)
    
    if not counts.exists():
        print("No se encontraron responsables con múltiples turnos.")
        return

    for resp in counts:
        print(f"\nResponsable: {resp.nombre} (ID: {resp.id}) - Turnos: {resp.num_turnos}")
        turnos = resp.turnos.all().order_by('fecha', 'hora')
        
        for t in turnos:
            eqs = t.equipos.all()
            print(f"  > Turno ID: {t.id} | Fecha: {t.fecha} | Hora: {t.hora} | Estado: {t.estado}")
            print(f"    Equipos del Turno ({eqs.count()}):")
            for e in eqs:
                print(f"      - [{e.id}] {e.marca} {e.modelo} ({e.codigo})")
        
        # Verificar si hay equipos vinculados al responsable que no pertenecen a sus turnos (o que pertenecen a otros)
        all_resp_eqs = resp.equipos.all()
        print(f"    Total Equipos vinculados al Responsable (directo): {all_resp_eqs.count()}")
        
if __name__ == "__main__":
    debug_duplicates()
