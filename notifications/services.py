from datetime import datetime, timedelta, time
from django.utils import timezone
from django.db.models import Q
from django.conf import settings
from django.core.mail import send_mail, get_connection
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from .models import ConfiguracionNotificacion, HistorialEnvio
from core.models import Turno

class NotificationService:
    
    @staticmethod
    def calcular_proyeccion(dias=7):
        """
        Calcula qué notificaciones se enviarían en los próximos 'dias' según las reglas actuales.
        No guarda nada en BD. Retorna una lista de diccionarios (objetos virtuales).
        """
        config = ConfiguracionNotificacion.get_solo()
        now = timezone.now()
        end_date = now + timedelta(days=dias)
        
        # 1. Obtener turnos relevantes (Excluyendo Cancelados y Completados)
        turnos = Turno.objects.filter(
            fecha__gte=now.date(), 
            fecha__lte=(end_date.date() + timedelta(days=config.dias_antes + 1))
        ).exclude(
            estado__in=['cancelado', 'completado']
        ).select_related('responsable')

        proyeccion = []
        
        for turno in turnos:
            if not turno.fecha or not turno.hora or not turno.responsable.email:
                continue
                
            # Make aware to allow comparison with timezone.now()
            try:
                turno_dt = timezone.make_aware(datetime.combine(turno.fecha, turno.hora))
            except Exception:
                turno_dt = datetime.combine(turno.fecha, turno.hora)
            
            # Regla 1: Anticipado
            if config.activar_anticipado:
                fecha_notif = turno_dt - timedelta(days=config.dias_antes)
                if now <= fecha_notif <= end_date:
                    proyeccion.append({
                        'turno': turno,
                        'tipo': 'anticipado',
                        'tipo_display': 'Recordatorio Anticipado',
                        'fecha_programada': fecha_notif,
                        'responsable': turno.responsable,
                        'estado_simulado': 'pendiente'
                    })
            
            # Regla 2: Día del Turno (X minutos antes)
            if config.activar_jornada:
                fecha_notif = turno_dt - timedelta(minutes=config.minutos_antes_jornada)
                if now <= fecha_notif <= end_date:
                    proyeccion.append({
                        'turno': turno,
                        'tipo': 'jornada',
                        'tipo_display': 'Aviso Próximo (Hoy)',
                        'fecha_programada': fecha_notif,
                        'responsable': turno.responsable,
                        'estado_simulado': 'pendiente'
                    })
                    
        # Ordenar por fecha
        proyeccion.sort(key=lambda x: x['fecha_programada'])
        return proyeccion

    @staticmethod
    def ejecutar_vigilancia():
        """
        EL OBSERVADOR.
        Busca turnos que deben ser notificados AHORA (ventana de +- 1 hora o intervalo de cron).
        Verifica si ya se envió para no duplicar.
        Envía y registra en Historial.
        """
        config = ConfiguracionNotificacion.get_solo()
        now = timezone.now()
        # Ventana de disparo: Notificaciones programadas para "hace poco" que no se hayan enviado.
        # Digamos, desde hace 1 hora hasta ahora. (Asumiendo cron cada hora).
        ventana_inicio = now - timedelta(hours=1, minutes=30)
        ventana_fin = now + timedelta(minutes=5) # Un poco de margen futuro
        
        enviados = 0
        errores = 0
        
        # Obtenemos candidatos
        turnos_candidatos = Turno.objects.filter(
            fecha__gte=now.date() - timedelta(days=config.dias_antes + 1),
            fecha__lte=now.date() + timedelta(days=2)
        ).exclude(
            estado__in=['cancelado', 'completado']
        ).select_related('responsable', 'responsable__equipos')

        connection = get_connection()
        connection.open()
        
        for turno in turnos_candidatos:
            if not turno.fecha or not turno.hora or not turno.responsable.email:
                continue
            
            try:
                turno_dt = timezone.make_aware(datetime.combine(turno.fecha, turno.hora))
            except:
                turno_dt = datetime.combine(turno.fecha, turno.hora)
                
            triggers = []

            # Evaluar Regla 1: Anticipado
            if config.activar_anticipado:
                fecha_target = turno_dt - timedelta(days=config.dias_antes)
                if ventana_inicio <= fecha_target <= ventana_fin:
                    triggers.append(('anticipado', 'Recordatorio Anticipado'))

            # Evaluar Regla 2: Día del Turno (X minutos antes)
            if config.activar_jornada:
                fecha_target = turno_dt - timedelta(minutes=config.minutos_antes_jornada)
                if ventana_inicio <= fecha_target <= ventana_fin:
                    triggers.append(('jornada', 'Aviso Próximo (Hoy)'))
            
            for tipo_cod, tipo_nombre in triggers:
                # VERIFICAR DUPLICADOS EN HISTORIAL
                ya_enviado = HistorialEnvio.objects.filter(
                    turno=turno,
                    tipo=tipo_cod,
                    estado='enviado'
                ).exists()
                
                if ya_enviado:
                    continue
                    
                # PROCEDER AL ENVÍO
                try:
                    # Lógica mejorada para {evento}
                    evento_desc = ""
                    # 1. Descripción del turno (si existe en el modelo, aunque Turno actual no la tiene, preparamos)
                    if hasattr(turno, 'descripcion') and turno.descripcion:
                        evento_desc = turno.descripcion
                    # 2. Descripción del equipo del funcionario
                    elif turno.responsable.equipos.exists():
                        evento_desc = turno.responsable.equipos.first().descripcion
                    
                    # 3. Fallback a Actividad General de la configuración
                    if not evento_desc or str(evento_desc).lower() == 'nan':
                        evento_desc = config.actividad_general

                    fmt_data = {
                        'evento': evento_desc,
                        'fecha': turno.fecha.strftime("%d/%m/%Y") if turno.fecha else "-",
                        'hora': turno.hora.strftime("%H:%M") if turno.hora else "-",
                        'funcionario': turno.responsable.nombre
                    }

                    # Render Subject
                    subject = config.asunto_template.format(**fmt_data)
                    
                    # Render Body (Plain text first for formatting, then wrap in HTML if needed)
                    body_text = config.cuerpo_template.format(**fmt_data)

                    context = {
                        'turno': turno,
                        'responsable': turno.responsable,
                        'tipo_nombre': tipo_nombre,
                        'cuerpo_personalizado': body_text
                    }
                    
                    # Creamos el objeto Historial
                    historial = HistorialEnvio(
                        turno=turno,
                        tipo=tipo_cod,
                        destinatario=turno.responsable.email,
                        asunto=subject,
                        cuerpo=body_text,
                        estado='enviado'
                    )
                    
                    # Render HTML template (The template will now use 'cuerpo_personalizado')
                    html_message = render_to_string('notifications/email_template.html', context)
                    plain_message = strip_tags(html_message)
                    
                    send_mail(
                        subject,
                        plain_message,
                        settings.DEFAULT_FROM_EMAIL,
                        [turno.responsable.email],
                        html_message=html_message,
                        connection=connection,
                        fail_silently=False
                    )
                    
                    # Para soportar BCC en send_mail de Django (que no tiene bcc param directo en esta firma rápida)
                    # Usaremos EmailMultiAlternatives para mayor control si es necesario, 
                    # pero por ahora, si hay CC, lo añadimos a la lista si es BCC real
                    if config.cc_email:
                        send_mail(
                            f"[BCC] {subject}",
                            plain_message,
                            settings.DEFAULT_FROM_EMAIL,
                            [config.cc_email],
                            html_message=html_message,
                            connection=connection,
                            fail_silently=True
                        )
                    
                    historial.save()
                    enviados += 1
                    
                except Exception as e:
                    print(f"Error enviando {turno}: {e}")
                    # Si falló antes de guardar historial, no pasa nada (se reintentará prox ciclo)
                    # Si falló después (en send_mail), actualizamos historial a fallido
                    if 'historial' in locals() and historial.pk:
                        historial.estado = 'fallido'
                        historial.error_log = str(e)
                        historial.save()
                    errores += 1

        connection.close()
        return enviados, errores

    @staticmethod
    def enviar_broadcast_inicio():
        """
        Envía un correo masivo de inicio a todos los funcionarios con turnos.
        """
        config = ConfiguracionNotificacion.get_solo()
        fuincionarios_con_turnos = Turno.objects.exclude(
            estado__in=['cancelado', 'completado']
        ).values_list('responsable__email', flat=True).distinct()
        
        emails = [e for e in fuincionarios_con_turnos if e]
        
        if not emails:
            return 0
            
        subject = config.asunto_inicio
        body_text = config.cuerpo_inicio
        
        # Opcional: Podríamos usar la plantilla HTML simple
        html_message = f"""
        <div style='font-family: sans-serif; padding: 20px; color: #374151;'>
            <h2 style='color: #2563eb;'>{subject}</h2>
            <div style='white-space: pre-wrap;'>{body_text}</div>
            <hr style='border: none; border-top: 1px solid #e5e7eb; margin: 20px 0;'>
            <p style='font-size: 11px; color: #9ca3af;'>Este es una notificación automática de inicio de cronograma.</p>
        </div>
        """
        
        send_mail(
            subject,
            strip_tags(html_message),
            settings.DEFAULT_FROM_EMAIL,
            emails,
            html_message=html_message,
            fail_silently=False
        )

        if config.cc_email:
            send_mail(
                f"[BCC-INICIO] {subject}",
                strip_tags(html_message),
                settings.DEFAULT_FROM_EMAIL,
                [config.cc_email],
                html_message=html_message,
                fail_silently=True
            )
        
        return len(emails)
