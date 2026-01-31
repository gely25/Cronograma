import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gestion_activos.settings")
django.setup()
from django.core.mail import send_mail
from django.conf import settings

print(f"DEBUG: Backend={settings.EMAIL_BACKEND}")
print(f"DEBUG: Host={settings.EMAIL_HOST}")
print(f"DEBUG: Port={settings.EMAIL_PORT}")
print(f"DEBUG: User={settings.EMAIL_HOST_USER}")

try:
    res = send_mail(
        'Test SMTP',
        'Cuerpo de prueba',
        settings.DEFAULT_FROM_EMAIL,
        ['asegoviac3@unemi.edu.ec'],
        fail_silently=False
    )
    print(f"RESULTADO: {res}")
except Exception as e:
    print(f"ERROR: {e}")
