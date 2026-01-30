from datetime import datetime, timedelta, time
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db.models import Q
from django.conf import settings
from django.core.mail import send_mail, get_connection, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.core.mail import EmailMultiAlternatives
from email.mime.image import MIMEImage
import os
from .models import ConfiguracionNotificacion, HistorialEnvio, NotificacionEncolada, AuditLogNotificaciones
from core.models import Turno

class NotificationService:

    @staticmethod
    def sincronizar_cola():
        """
        Escanea los turnos próximos y genera los registros en NotificacionEncolada 
        que aún no existan.
        """
        config = ConfiguracionNotificacion.get_solo()
        now = timezone.now()
        # Escaneamos con un margen razonable (7 días)
        ventana_dias = 7
        # Fix: Use localdate
        local_today = timezone.localdate(now)
        end_date = local_today + timedelta(days=ventana_dias)
        
        turnos = Turno.objects.filter(
            fecha__gte=local_today - timedelta(days=1),
            fecha__lte=end_date + timedelta(days=config.dias_antes + 1)
        ).exclude(
            estado__in=['cancelado', 'completado']
        ).select_related('responsable')

        creadas = 0
        for turno in turnos:
            if not turno.fecha or not turno.hora or not turno.responsable.email:
                continue
            
            try:
                turno_dt = timezone.make_aware(datetime.combine(turno.fecha, turno.hora))
            except:
                turno_dt = datetime.combine(turno.fecha, turno.hora)

            # Regla 1: Anticipado
            if config.activar_anticipado:
                prog = turno_dt - timedelta(days=config.dias_antes)
                # Solo planificar si es futuro o muy reciente para ejecutar_vigilancia
                if prog > (now - timedelta(hours=1)):
                    obj, created = NotificacionEncolada.objects.get_or_create(
                        turno=turno,
                        tipo='anticipado',
                        defaults={'fecha_programada': prog}
                    )
                    if created: creadas += 1

            # Regla 2: Día del Turno
            if config.activar_jornada:
                prog = turno_dt - timedelta(minutes=config.minutos_antes_jornada)
                if prog > (now - timedelta(minutes=30)):
                    obj, created = NotificacionEncolada.objects.get_or_create(
                        turno=turno,
                        tipo='jornada',
                        defaults={'fecha_programada': prog}
                    )
                    if created: creadas += 1
        
        return creadas
    @staticmethod
    def calcular_proyeccion(dias=7):
        """
        Calcula qué notificaciones corresponden a los turnos en los próximos 'dias'.
        Enfoque centrado en TURNOS para que el usuario vea todo su plan.
        """
        config = ConfiguracionNotificacion.get_solo()
        now = timezone.now()
        # Rango basado en fechas de TURNOS
        # CRITICAL FIX: Use localdate() because in UTC it might be tomorrow, 
        # causing today's evening shifts to be skipped in the projection.
        start_date = timezone.localdate(now)
        end_date = start_date + timedelta(days=dias)
        
        # 1. Obtener todos los turnos del rango (incluyendo completados si el usuario los quiere ver, 
        # pero para notificar solemos omitir completados/cancelados)
        turnos = Turno.objects.filter(
            fecha__gte=start_date, 
            fecha__lte=end_date
        ).exclude(
            estado='cancelado' # Quitamos solo los cancelados explícitos
        ).select_related('responsable').order_by('fecha', 'hora')

        proyeccion = []
        
        for turno in turnos:
            if not turno.fecha or not turno.hora or not turno.responsable.email:
                continue
                
            try:
                turno_dt = timezone.make_aware(datetime.combine(turno.fecha, turno.hora))
            except:
                turno_dt = datetime.combine(turno.fecha, turno.hora)
            
            # Solo generamos proyección si el turno aún no ha ocurrido O si es hoy
            if turno_dt < (now - timedelta(hours=2)): # Margen de 2 horas tras el turno
                continue

            # Regla 1: Anticipado
            if config.activar_anticipado:
                fecha_notif = turno_dt - timedelta(days=config.dias_antes)
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
                proyeccion.append({
                    'turno': turno,
                    'tipo': 'jornada',
                    'tipo_display': 'Aviso Próximo (Hoy)',
                    'fecha_programada': fecha_notif,
                    'responsable': turno.responsable,
                    'estado_simulado': 'pendiente'
                })
                    
        # Ordenar por fecha de notificación
        proyeccion.sort(key=lambda x: x['fecha_programada'])
        return proyeccion

    @staticmethod
    def ejecutar_vigilancia():
        """
        EL PROCESADOR DE COLA.
        Busca notificaciones en 'pendiente' o 'error_temporal' con fecha_programada <= ahora.
        Realiza el envío, gestiona reintentos y actualiza estados.
        """
        now = timezone.now()
        cola = NotificacionEncolada.objects.filter(
            estado__in=['pendiente', 'error_temporal'],
            fecha_programada__lte=now
        ).select_related('turno', 'turno__responsable')
        
        enviados = 0
        errores = 0
        
        if not cola.exists():
            return 0, 0

        config = ConfiguracionNotificacion.get_solo()
        connection = get_connection()
        connection.open()
        
        # Imagen para adjuntar (Leída una sola vez para optimizar velocidad)
        img_path = os.path.join(settings.BASE_DIR, 'core', 'static', 'img', 'mujeru.jpg')
        img_data = None
        if os.path.exists(img_path):
            with open(img_path, 'rb') as f:
                img_data = f.read()
        
        for item in cola:
            item.estado = 'procesando'
            item.save()
            
            try:
                turno = item.turno
                # Lógica de {evento}
                evento_desc = ""
                if hasattr(turno, 'descripcion') and turno.descripcion:
                    evento_desc = turno.descripcion
                elif turno.responsable.equipos.exists():
                    evento_desc = turno.responsable.equipos.first().descripcion
                
                if not evento_desc or str(evento_desc).lower() == 'nan':
                    evento_desc = config.actividad_general

                # Lógica dinámica avanzada (Multidispositivo y Tiempos reales)
                equipos = turno.responsable.equipos.all()
                if equipos.count() > 1:
                    lista_equipos = "\n".join([f"• {e.marca} {e.modelo} (Cód: {e.codigo or 'N/A'})" for e in equipos])
                    marca_str = "varios modelos"
                    modelo_str = "ver detalle en lista"
                elif equipos.count() == 1:
                    e = equipos.first()
                    lista_equipos = f"• {e.marca} {e.modelo} (Cód: {e.codigo or 'N/A'})"
                    marca_str = e.marca
                    modelo_str = e.modelo
                else:
                    lista_equipos = "Equipo informático general"
                    marca_str = "Hardware"
                    modelo_str = "General"
                
                # Para evitar 'nan' de pandas/excel
                marca_str = str(marca_str) if str(marca_str).lower() != 'nan' else "Hardware"
                modelo_str = str(modelo_str) if str(modelo_str).lower() != 'nan' else "General"
                
                # Fechas dinámicas del cronograma
                config_crono = getattr(settings, 'CONFIG_CRONOGRAMA', None) # Intento obtener de settings o fallback
                # Pero mejor lo sacamos de la DB si es posible
                from core.models import ConfiguracionCronograma
                crono = ConfiguracionCronograma.objects.first()
                duracion = crono.duracion_turno if crono else 30
                
                fmt_data = {
                    'evento': evento_desc,
                    'fecha': turno.fecha.strftime("%d/%m/%Y"),
                    'hora': turno.hora.strftime("%H:%M %p"),
                    'funcionario': turno.responsable.nombre,
                    'marca': marca_str,
                    'modelo': modelo_str,
                    'equipos_lista': lista_equipos,
                    'duracion': duracion,
                    'fecha_turno': turno.fecha.strftime("%d de %B del %Y") # Formato más formal
                }

                subject = config.asunto_template.format(**fmt_data)
                body_text = config.cuerpo_template.format(**fmt_data)

                context = {
                    'turno': turno,
                    'responsable': turno.responsable,
                    'tipo_nombre': item.get_tipo_display(),
                    'cuerpo_personalizado': body_text
                }
                
                html_message = render_to_string('notifications/email_template.html', context)
                plain_message = strip_tags(html_message)
                
                msg = EmailMultiAlternatives(
                    subject,
                    plain_message,
                    settings.DEFAULT_FROM_EMAIL,
                    [turno.responsable.email],
                    connection=connection
                )
                msg.attach_alternative(html_message, "text/html")

                if img_data:
                    img = MIMEImage(img_data)
                    img.add_header('Content-ID', '<header_image>')
                    msg.attach(img)
                
                msg.send()

                # BCC de supervisión
                if config.cc_email:
                    try:
                        bcc_msg = EmailMultiAlternatives(
                            f"[BCC] {subject}",
                            plain_message,
                            settings.DEFAULT_FROM_EMAIL,
                            [config.cc_email],
                            connection=connection
                        )
                        bcc_msg.attach_alternative(html_message, "text/html")
                        if img_data:
                            img_copy = MIMEImage(img_data)
                            img_copy.add_header('Content-ID', '<header_image>')
                            bcc_msg.attach(img_copy)
                        bcc_msg.send()
                    except:
                        pass # No bloquear si el BCC falla

                # ÉXITO
                item.estado = 'enviado'
                item.intentos += 1
                item.ultimo_error = ""
                item.save()
                
                HistorialEnvio.objects.create(
                    notificacion=item,
                    turno=turno,
                    tipo=item.tipo,
                    intento_n=item.intentos,
                    estado='enviado',
                    destinatario=turno.responsable.email,
                    asunto=subject,
                    cuerpo=body_text
                )
                enviados += 1
                
            except Exception as e:
                item.intentos += 1
                item.ultimo_error = str(e)
                # Decidir si agotamos intentos o reintentamos luego
                if item.intentos >= item.max_intentos:
                    item.estado = 'fallido'
                else:
                    item.estado = 'error_temporal'
                item.save()
                
                HistorialEnvio.objects.create(
                    notificacion=item,
                    turno=item.turno,
                    tipo=item.tipo,
                    intento_n=item.intentos,
                    estado='fallido',
                    destinatario=item.turno.responsable.email if item.turno else "??",
                    asunto="Error en envío automático",
                    error_log=str(e)
                )
                errores += 1

        connection.close()
        return enviados, errores


    @staticmethod
    def reenviar_individual(cola_id):
        """
        Reintenta enviar una notificación específica de la cola.
        """
        item = get_object_or_404(NotificacionEncolada, id=cola_id)
        # Forzar estado a pendiente para que ejecutar_vigilancia lo tome,
        # o procesarlo inmediatamente. Vamos a procesarlo inmediatamente.
        
        connection = get_connection()
        connection.open()
        
        # Imagen para adjuntar
        img_path = os.path.join(settings.BASE_DIR, 'core', 'static', 'img', 'mujeru.jpg')
        
        try:
            turno = item.turno
            config = ConfiguracionNotificacion.get_solo()
            
            # Datos para el template
            # (Usamos la misma lógica que ejecutar_vigilancia)
            evento_desc = ""
            if hasattr(turno, 'descripcion') and turno.descripcion:
                evento_desc = turno.descripcion
            elif turno.responsable.equipos.exists():
                evento_desc = turno.responsable.equipos.first().descripcion
            
            if not evento_desc or str(evento_desc).lower() == 'nan':
                evento_desc = config.actividad_general

            # Lógica dinámica en reenvío (Multidispositivo y Tiempos reales)
            equipos = turno.responsable.equipos.all()
            if equipos.count() > 1:
                lista_equipos = "\n".join([f"• {e.marca} {e.modelo} (Cód: {e.codigo or 'N/A'})" for e in equipos])
                marca_str = "varios modelos"
                modelo_str = "ver detalle en lista"
            elif equipos.count() == 1:
                e = equipos.first()
                lista_equipos = f"• {e.marca} {e.modelo} (Cód: {e.codigo or 'N/A'})"
                marca_str = e.marca
                modelo_str = e.modelo
            else:
                lista_equipos = "Equipo informático general"
                marca_str = "Hardware"
                modelo_str = "General"
            
            marca_str = str(marca_str) if str(marca_str).lower() != 'nan' else "Hardware"
            modelo_str = str(modelo_str) if str(modelo_str).lower() != 'nan' else "General"
            
            from core.models import ConfiguracionCronograma
            crono = ConfiguracionCronograma.objects.first()
            duracion = crono.duracion_turno if crono else 30

            fmt_data = {
                'evento': evento_desc,
                'fecha': turno.fecha.strftime("%d/%m/%Y"),
                'hora': turno.hora.strftime("%H:%M %p"),
                'funcionario': turno.responsable.nombre,
                'marca': marca_str,
                'modelo': modelo_str,
                'equipos_lista': lista_equipos,
                'duracion': duracion,
                'fecha_turno': turno.fecha.strftime("%d de %B del %Y")
            }

            subject = config.asunto_template.format(**fmt_data)
            body_text = config.cuerpo_template.format(**fmt_data)

            context = {
                'turno': turno,
                'responsable': turno.responsable,
                'tipo_nombre': item.get_tipo_display(),
                'cuerpo_personalizado': body_text
            }
            
            html_message = render_to_string('notifications/email_template.html', context)
            plain_message = strip_tags(html_message)
            
            msg = EmailMultiAlternatives(
                subject,
                plain_message,
                settings.DEFAULT_FROM_EMAIL,
                [turno.responsable.email],
                connection=connection
            )
            msg.attach_alternative(html_message, "text/html")

            if os.path.exists(img_path):
                with open(img_path, 'rb') as f:
                    img = MIMEImage(f.read())
                    img.add_header('Content-ID', '<header_image>')
                    msg.attach(img)
            
            msg.send()
            
            # ÉXITO
            item.estado = 'enviado'
            item.ultimo_error = ""
            item.intentos += 1
            item.save()
            
            HistorialEnvio.objects.create(
                notificacion=item,
                turno=turno,
                tipo=item.tipo,
                intento_n=item.intentos,
                estado='enviado',
                destinatario=turno.responsable.email,
                asunto=subject,
                cuerpo=body_text
            )
            
            # Auditoría humana
            AuditLogNotificaciones.objects.create(
                notificacion=item,
                accion="Reenvío Individual Manual",
                detalles=f"Reenviado con éxito a {turno.responsable.email}"
            )
            
            connection.close()
            return True, "Enviado con éxito"
            
        except Exception as e:
            item.intentos += 1
            item.ultimo_error = str(e)
            item.save()
            
            HistorialEnvio.objects.create(
                notificacion=item,
                turno=item.turno,
                tipo=item.tipo,
                intento_n=item.intentos,
                estado='fallido',
                destinatario=item.turno.responsable.email if item.turno else "??",
                asunto="Reenvío Manual",
                error_log=str(e)
            )
            
            connection.close()
            return False, str(e)

    @staticmethod
    def reenviar_masivo(id_list):
        """
        Reintenta el envío de múltiples notificaciones de la cola.
        """
        exitos = 0
        errores = 0
        for cola_id in id_list:
            success, _ = NotificationService.reenviar_individual(cola_id)
            if success:
                exitos += 1
            else:
                errores += 1
        return exitos, errores
