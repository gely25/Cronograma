import os
import django
import json
from datetime import date, timedelta

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gestion_activos.settings')
django.setup()

from core.models import ConfiguracionCronograma, Turno, Responsable
from django.test import RequestFactory
from core.views import guardar_configuracion, generar_cronograma_view

def test_date_validation():
    print("TEST: Date validation")
    factory = RequestFactory()
    # Case: Inicio > Fin
    data = {
        'fecha_inicio': '2026-02-28',
        'fecha_fin': '2026-02-27',
    }
    request = factory.post('/config/guardar/', data)
    response = guardar_configuracion(request)
    result = json.loads(response.content)
    print(f"  - Response status: {response.status_code}")
    print(f"  - Response body: {result}")
    if response.status_code == 400 and 'fecha de inicio no puede ser posterior' in result['message']:
        print("  - SUCCESS: Validation caught invalid dates.")
    else:
        print("  - FAILURE: Validation failed to catch invalid dates.")

def test_no_pending_turns_reporting():
    print("\nTEST: No pending turns reporting")
    # Ensure 0 pending turns
    Turno.objects.filter(estado='pendiente').update(estado='asignado')
    
    factory = RequestFactory()
    request = factory.post('/cronograma/generar/')
    response = generar_cronograma_view(request)
    result = json.loads(response.content)
    print(f"  - Response status: {response.status_code}")
    print(f"  - Response body: {result}")
    
    if response.status_code == 400 and result['data']['error_type'] == 'no_pending_turns':
        print("  - SUCCESS: Reported 'no_pending_turns' correctly.")
    else:
        print("  - FAILURE: Did not report 'no_pending_turns' as expected.")

if __name__ == "__main__":
    test_date_validation()
    test_no_pending_turns_reporting()
