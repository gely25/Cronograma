
import os
import django
import sys

# Setup Django
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gestion_activos.settings')
django.setup()

from core.models import Turno, Responsable, Equipo

def debug_duplicates():
    print("--- DIAGNÓSTICO DETALLADO ---")
    
    # 1. Listar todos los Turnos de un responsable duplicado (ej: el que tenga más turnos)
    resps = Responsable.objects.annotate(n=django.db.models.Count('turnos')).filter(n__gt=1).order_by('-n')
    
    if not resps.exists():
        print("No hay responsables con múltiples turnos.")
        return

    for r in resps:
        print(f"\nPERSONA: {r.nombre} (Total Turnos: {r.n})")
        
        # Equipos directos del Responsable
        direct_eqs = r.equipos.all()
        print(f"Equipos ligados directamente al Responsable: {direct_eqs.count()}")
        for e in direct_eqs:
            print(f"  - Equipo ID {e.id} | Vinculado a Turno ID: {e.turno_id} | {e.marca} {e.modelo}")

        print("\nDesglose por Turno:")
        for t in r.turnos.all().order_by('fecha', 'hora'):
            eqs = t.equipos.all()
            print(f"  [TURNO {t.id}] Fecha: {t.fecha} | Equipos: {eqs.count()}")
            for e in eqs:
                print(f"    - [{e.id}] {e.marca} {e.modelo}")
                
if __name__ == "__main__":
    debug_duplicates()
