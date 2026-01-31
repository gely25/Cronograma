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
    def calcular_proyeccion(dias=7, offset=0):
        """
        Calcula qué notificaciones corresponden a los turnos en el rango [offset, offset+dias].
        Enfoque centrado en TURNOS para que el usuario vea todo su plan.
        """
        config = ConfiguracionNotificacion.get_solo()
        now = timezone.now()
        # Rango basado en fechas de TURNOS
        # CRITICAL FIX: Use localdate() because in UTC it might be tomorrow, 
        # causing today's evening shifts to be skipped in the projection.
        local_today = timezone.localdate(now)
        start_date = local_today + timedelta(days=offset)
        end_date = start_date + timedelta(days=dias)
        
        # 1. Obtener todos los turnos del rango (incluyendo completados si el usuario los quiere ver, 
        # pero para notificar solemos omitir completados/cancelados)
        # Ampliamos búsqueda de turnos para capturar aquellos cuya NOTIFICACIÓN caiga en el rango
        # aunque el turno sea futuro.
        turnos = Turno.objects.filter(
            fecha__gte=start_date, 
            fecha__lte=end_date + timedelta(days=30) # Lookahead generoso para anticipados
        ).exclude(
            estado='cancelado' # Quitamos solo los cancelados explícitos
        ).select_related('responsable').order_by('fecha', 'hora')

        # 2. Obtener lo que ya está en cola para este rango (optimización: una sola consulta)
        en_cola = NotificacionEncolada.objects.filter(
            turno__in=turnos
        ).values('turno_id', 'tipo', 'estado')
        memo_cola = {(c['turno_id'], c['tipo']): c['estado'] for c in en_cola}

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
                if start_date <= timezone.localdate(fecha_notif) <= end_date:
                    estado_real = memo_cola.get((turno.id, 'anticipado'))
                    proyeccion.append({
                        'turno': turno,
                        'tipo': 'anticipado',
                        'tipo_display': 'Recordatorio Anticipado',
                        'fecha_programada': fecha_notif,
                        'responsable': turno.responsable,
                        'estado_actual': estado_real,
                        'ya_procesado': estado_real is not None and estado_real != 'cancelado'
                    })
            
            # Regla 2: Día del Turno (X minutos antes)
            if config.activar_jornada:
                fecha_notif = turno_dt - timedelta(minutes=config.minutos_antes_jornada)
                if start_date <= timezone.localdate(fecha_notif) <= end_date:
                    estado_real = memo_cola.get((turno.id, 'jornada'))
                    proyeccion.append({
                        'turno': turno,
                        'tipo': 'jornada',
                        'tipo_display': 'Aviso Próximo (Hoy)',
                        'fecha_programada': fecha_notif,
                        'responsable': turno.responsable,
                        'estado_actual': estado_real,
                        'ya_procesado': estado_real is not None and estado_real != 'cancelado'
                    })
                    
        # Ordenar por fecha de notificación
        proyeccion.sort(key=lambda x: x['fecha_programada'])
        return proyeccion

    @staticmethod
    def ejecutar_vigilancia(specific_ids=None):
        """
        EL PROCESADOR DE COLA.
        Busca notificaciones en 'pendiente' o 'error_temporal'.
        Si specific_ids es proveído, ignora la fecha_programada.
        """
        now = timezone.now()
        
        if specific_ids:
            cola = NotificacionEncolada.objects.filter(
                id__in=specific_ids,
                estado__in=['pendiente', 'error_temporal', 'enviado', 'fallido', 'cancelado'] # Permitir forzar cualquiera
            ).select_related('turno', 'turno__responsable')
        else:
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
        
        # Imagen de encabezado
        header_img_path = os.path.join(settings.BASE_DIR, 'core', 'static', 'img', 'mujeru.jpg')
        header_img_data = None
        if os.path.exists(header_img_path):
            with open(header_img_path, 'rb') as f:
                header_img_data = f.read()
        
        # Logo UNEMI para firma
        logo_path = os.path.join(settings.BASE_DIR, 'unemi.png')
        logo_data = None
        if os.path.exists(logo_path):
            with open(logo_path, 'rb') as f:
                logo_data = f.read()
        
        for item in cola:
            item.estado = 'procesando'
            item.save()
            
            try:
                turno = item.turno
                if not turno or not turno.responsable or not turno.responsable.email:
                    raise Exception("El responsable no tiene un correo electrónico configurado.")

                if not settings.DEFAULT_FROM_EMAIL:
                    raise Exception("El sistema no tiene configurado el correo emisor (DEFAULT_FROM_EMAIL en settings.py).")

                try:
                    subject, body_text = NotificationService._preparar_contenido(item, config)
                except Exception as e:
                    raise e

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

                if header_img_data:
                    h_img = MIMEImage(header_img_data)
                    h_img.add_header('Content-ID', '<header_image>')
                    msg.attach(h_img)

                if logo_data:
                    l_img = MIMEImage(logo_data)
                    l_img.add_header('Content-ID', '<unemi_logo>')
                    msg.attach(l_img)
                
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
                        if header_img_data:
                            h_img_copy = MIMEImage(header_img_data)
                            h_img_copy.add_header('Content-ID', '<header_image>')
                            bcc_msg.attach(h_img_copy)
                        if logo_data:
                            l_img_copy = MIMEImage(logo_data)
                            l_img_copy.add_header('Content-ID', '<unemi_logo>')
                            bcc_msg.attach(l_img_copy)
                        bcc_msg.send()
                    except:
                        pass # No bloquear si el BCC falla

                msg.send()

                # ÉXITO
                item.estado = 'enviado'
                item.intentos += 1
                item.ultimo_error = ""
                item.save()
                
                # Snapshot de auditoría
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
                
                # Log de auditoría humana (para trazabilidad total)
                from .models import AuditLogNotificaciones
                AuditLogNotificaciones.objects.create(
                    notificacion=item,
                    accion="Envío Automático / Batch",
                    detalles=f"Notificación enviada con éxito al funcionario. Intento {item.intentos}."
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
            if not turno or not turno.responsable or not turno.responsable.email:
                raise Exception("El responsable no tiene un correo electrónico configurado.")

            if not settings.DEFAULT_FROM_EMAIL:
                raise Exception("El sistema no tiene configurado el correo emisor (DEFAULT_FROM_EMAIL en settings.py).")

            config = ConfiguracionNotificacion.get_solo()
            subject, body_text = NotificationService._preparar_contenido(item, config)
        except Exception as e:
            item.ultimo_error = str(e)
            item.save()
            raise e

        try:
            turno = item.turno
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
            
            # Logo UNEMI para firma
            logo_path = os.path.join(settings.BASE_DIR, 'unemi.png')
            if os.path.exists(logo_path):
                with open(logo_path, 'rb') as f:
                    l_img = MIMEImage(f.read())
                    l_img.add_header('Content-ID', '<unemi_logo>')
                    msg.attach(l_img)
            
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

    @staticmethod
    def _preparar_contenido(item, config):
        """
        Helper para centralizar la lógica de formateo de templates.
        """
        turno = item.turno
        # Lógica de {evento}
        evento_desc = ""
        if hasattr(turno, 'descripcion') and turno.descripcion:
            evento_desc = turno.descripcion
        elif turno.responsable.equipos.exists():
            evento_desc = turno.responsable.equipos.first().descripcion
        
        if not evento_desc or str(evento_desc).lower() == 'nan':
            evento_desc = config.actividad_general

        # Lógica dinámica avanzada
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
        
        meses = {
            1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
            5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
            9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"
        }
        mes_nombre = meses[turno.fecha.month]
        fecha_formal = f"{turno.fecha.day} de {mes_nombre} del {turno.fecha.year}"
        
        # Formatear hora (cambiar AM/PM a p.m./a.m. si se desea un toque más local, o dejar %p)
        hora_str = turno.hora.strftime("%I:%M %p").replace('AM', 'a.m.').replace('PM', 'p.m.')

        fmt_data = {
            'evento': evento_desc,
            'fecha': turno.fecha.strftime("%d/%m/%Y"),
            'hora': hora_str,
            'funcionario': turno.responsable.nombre,
            'marca': marca_str,
            'modelo': modelo_str,
            'equipos_lista': lista_equipos,
            'duracion': duracion,
            'fecha_turno': fecha_formal
        }

        try:
            subject = config.asunto_template.format(**fmt_data)
            body_text = config.cuerpo_template.format(**fmt_data)
            return subject, body_text
        except KeyError as e:
            raise Exception(f"Falta el marcador de posición: {str(e)}. Verifique la configuración de la plantilla.")
        except ValueError as e:
            raise Exception(f"Error de formato en la plantilla: {str(e)}. Verifique el uso de llaves {{ }}.")
