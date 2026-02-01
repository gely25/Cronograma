import os
import django
import sys
from datetime import datetime, timedelta
from django.utils import timezone

# Setup Django
sys.path.append('c:\\Cronograma')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gestion_activos.settings')
django.setup()

from core.models import Turno, Responsable
from notifications.models import NotificacionEncolada, ConfiguracionNotificacion
from notifications.services import NotificationService

def debug_radar():
    config = ConfiguracionNotificacion.get_solo()
    print(f"RULES: Ant={config.activar_anticipado} Jor={config.activar_jornada}")
    proy = NotificationService.calcular_proyeccion(dias=7)
    print(f"RADAR_COUNT: {len(proy)}")
    for p in proy[:5]:
        print(f"Item: {p['tipo']} | Resp: {p['responsable'].nombre} | TurnoDate: {p['turno'].fecha}")

if __name__ == "__main__":
    debug_radar()
