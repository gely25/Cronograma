import os
import django
from django.utils import timezone
from datetime import timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gestion_activos.settings')
django.setup()

from core.models import Turno

now = timezone.localdate(timezone.now())
print(f"Today (Local): {now}")

ranges = [3, 7, 30]

for r in ranges:
    end = now + timedelta(days=r)
    count = Turno.objects.filter(fecha__gte=now, fecha__lte=end).exclude(estado='cancelado').count()
    print(f"Turns in next {r} days (until {end}): {count}")

# Show dates of next 10 turns
print("\n--- Next 10 Turns ---")
turns = Turno.objects.filter(fecha__gte=now).exclude(estado='cancelado').order_by('fecha')[:10]
for t in turns:
    print(f"{t.fecha} - {t.responsable.nombre}")
