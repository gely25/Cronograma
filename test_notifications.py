
import os
import django
from datetime import datetime, timedelta, time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gestion_activos.settings")
django.setup()

from core.models import Turno, Responsable, Equipo
from django.core.management import call_command
from django.utils import timezone

def run_test():
    print("Iniciando prueba de notificaciones...")
    
    # 1. Crear datos de prueba
    print("Creando datos de prueba...")
    responsable, _ = Responsable.objects.get_or_create(nombre="Test User Notification")
    
    # Limpiar turnos anteriores de este user
    Turno.objects.filter(responsable=responsable).delete()
    
    # Crear turno para mañana (notificacion debe ser hoy)
    mañana = timezone.now().date() + timedelta(days=1)
    turno = Turno.objects.create(
        responsable=responsable,
        fecha=mañana,
        hora=time(8, 0, 0),
        estado='asignado'
    )
    
    print(f"Turno creado: {turno}")
    print(f"Notificar el: {turno.notificar_el}")
    
    # Validar cálculo automático de notificar_el
    expected_notify = datetime.combine(mañana, time(8, 0, 0)) - timedelta(days=1)
    # Nota: timezone naive vs aware puede ser un tema, pero veamos.
    
    if turno.notificar_el and abs((turno.notificar_el.replace(tzinfo=None) - expected_notify).total_seconds()) < 60:
        print("✅ Cálculo de notificar_el correcto.")
    else:
        print(f"❌ Cálculo incorrecto. Esperado: {expected_notify}, Obtenido: {turno.notificar_el}")

    # Set notificar_el to slightly in the past to trigger sending
    turno.notificar_el = timezone.now() - timedelta(minutes=1)
    turno.save()
    
    print("Ejecutando comando send_notifications...")
    try:
        call_command('send_notifications')
        print("✅ Comando ejecutado.")
    except Exception as e:
        print(f"❌ Error ejecutando comando: {e}")
        
    turno.refresh_from_db()
    if turno.notificacion_enviada:
        print("✅ Notificación marcada como enviada.")
    else:
        print("❌ Notificación NO marcada como enviada.")

    # Limpieza
    turno.delete()
    responsable.delete()

if __name__ == "__main__":
    run_test()
