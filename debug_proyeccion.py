import os
import django
from datetime import timedelta
from django.utils import timezone

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gestion_activos.settings')
django.setup()

from notifications.services import NotificationService
from notifications.models import ConfiguracionNotificacion

config = ConfiguracionNotificacion.get_solo()
print(f"Config: dias_antes={config.dias_antes}, activar_anticipado={config.activar_anticipado}")

# Test Range 1: 3 Days (Offset 0)
print("\n--- Testing Range: 3 Days (Offset 0) ---")
proj = NotificationService.calcular_proyeccion(dias=3, offset=0)
print(f"Items found: {len(proj)}")
for p in proj[:5]:
    print(f" - {p['turno'].id} | Fecha Turno: {p['turno'].fecha} | Notif: {p['fecha_programada']} | Tipo: {p['tipo']}")

# Test Range 2: Next Week (Offset 3)
print("\n--- Testing Range: Next Week (Offset 3) ---")
proj = NotificationService.calcular_proyeccion(dias=7, offset=3)
print(f"Items found: {len(proj)}")
for p in proj[:5]:
    print(f" - {p['turno'].id} | Fecha Turno: {p['turno'].fecha} | Notif: {p['fecha_programada']} | Tipo: {p['tipo']}")
