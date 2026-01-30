import os
import django
from django.utils import timezone
from datetime import timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gestion_activos.settings')
django.setup()

from core.models import Turno

now = timezone.localdate(timezone.now())
print(f"Today: {now}")

for r in [3, 7, 30]:
    end = now + timedelta(days=r)
    count = Turno.objects.filter(fecha__gte=now, fecha__lte=end).exclude(estado='cancelado').count()
    print(f"Next {r} days: {count}")
