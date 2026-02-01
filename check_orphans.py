import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gestion_activos.settings')
django.setup()

from notifications.models import HistorialEnvio

total = HistorialEnvio.objects.count()
print(f"Total: {total}")

try:
    with_related = len(list(HistorialEnvio.objects.select_related('turno')))
    print(f"With Related (Iterated): {with_related}")
except Exception as e:
    print(f"Error iterating: {e}")

filter_count = HistorialEnvio.objects.select_related('turno').count()
print(f"With Related (Count): {filter_count}")
