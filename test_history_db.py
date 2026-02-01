import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gestion_activos.settings')
django.setup()
from notifications.models import HistorialEnvio

items = HistorialEnvio.objects.all()
print(f"Total items: {len(items)}")
for i in items:
    resp_name = "NONE"
    if i.turno and i.turno.responsable:
        resp_name = i.turno.responsable.nombre
    print(f"H-ID: {i.id}, Turno: {i.turno_id}, Resp: {resp_name}")
