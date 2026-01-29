from django.core.management.base import BaseCommand
from django.core.mail import send_mail
from django.utils import timezone
from django.conf import settings
from core.models import Turno
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Envía notificaciones de turnos pendientes'

    def handle(self, *args, **options):
        now = timezone.now()
        turnos_para_notificar = Turno.objects.filter(
            notificar_el__lte=now,
            notificacion_enviada=False,
            fecha__isnull=False
        )

        count = turnos_para_notificar.count()
        if count == 0:
            self.stdout.write(self.style.SUCCESS("No hay notificaciones pendientes."))
            return

        self.stdout.write(f"Procesando {count} notificaciones...")

        enviados = 0
        errores = 0

        for turno in turnos_para_notificar:
            try:
                # Construir el mensaje
                subject = f"Recordatorio de Turno: {turno.responsable.nombre}"
                message = f"""Hola {turno.responsable.nombre},

Este es un recordatorio de que tienes un turno asignado para el {turno.fecha} a las {turno.hora}.

Detalles del equipo:
Marca: {turno.responsable.equipos.first().marca if turno.responsable.equipos.exists() else 'N/A'}
Modelo: {turno.responsable.equipos.first().modelo if turno.responsable.equipos.exists() else 'N/A'}

Por favor, asegúrate de estar disponible.

Saludos,
El equipo de Gestión de Activos
"""
                
                # Enviar correo (TODO: Si el usuario tuviera email, usarlo. Por ahora, usando correo por defecto o hardcodeado para pruebas si se quisiera)
                # Asumiendo que se envía al correo configurado en settings o uno específico
                # Como no hay campo 'email' en Responsable, esto es un punto a aclarar. 
                # El usuario pidió "enviar notificaciones por correo", pero no dijo a quién. 
                # Asumiré envío a un correo de administrador o similar por ahora, o simularé el envío.
                # PERO, el código original de settings tenía valores reales de SMTP. 
                # Voy a poner un TODO o enviar al admin si no hay email en el modelo.
                # Revisando Modelos: Responsable solo tiene nombre.
                # Voy a enviar al ADMIN por defecto o al usuario si tuviera. 
                # Puesto que no hay email en responsable, esto probablemente fallará o requiere un email genérico.
                # Voy a usar DEFAULT_FROM_EMAIL como destinatario por ahora para probar, o logging.
                
                recipient_list = [settings.DEFAULT_FROM_EMAIL] # Placeholder hasta tener email real del usuario

                send_mail(
                    subject,
                    message,
                    settings.DEFAULT_FROM_EMAIL,
                    recipient_list,
                    fail_silently=False,
                )

                turno.notificacion_enviada = True
                turno.save()
                enviados += 1
                
            except Exception as e:
                logger.error(f"Error enviando notificación a {turno.responsable}: {str(e)}")
                self.stderr.write(self.style.ERROR(f"Error con {turno.responsable}: {e}"))
                errores += 1

        self.stdout.write(self.style.SUCCESS(f"Proceso finalizado. Enviados: {enviados}, Errores: {errores}"))
