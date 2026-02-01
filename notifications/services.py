import os
import time
from datetime import datetime, timedelta
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
            fecha__lte=end_date + timedelta(days=config.dias_antes + 1),
            estado__in=['pendiente', 'asignado']  # Filtrado Estricto: Solo pendientes y asignados
        ).select_related('responsable')

        creadas = 0
        for turno in turnos:
            if not turno.fecha or not turno.hora:
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
        AHORA FILTRA POR FECHA DE PROGRAMACIÓN (No fecha de turno).
        Range is [start_date, end_date) -> Exclusive end to avoid overlaps.
        """
        config = ConfiguracionNotificacion.get_solo()
        now = timezone.now()
        local_today = timezone.localdate(now)
        
        # Define window based on Notification Date
        start_date = local_today + timedelta(days=offset)
        end_date = start_date + timedelta(days=dias)
        
        # 1. Fetch Candidates: We need turns that MIGHT produce notifications in this window.
        # If config.dias_antes = 30, a turn in 30 days might trigger a notification today.
        # So we look ahead quite a bit (window end + max(dias_antes, 1) + buffer).
        lookahead = max(config.dias_antes, 1) + 5 
        
        turnos = Turno.objects.filter(
            fecha__gte=local_today - timedelta(days=1), # Include yesterday just in case of timezone edge cases
            fecha__lte=end_date + timedelta(days=lookahead), 
            estado__in=['pendiente', 'asignado']
        ).select_related('responsable').order_by('fecha', 'hora')

        # 2. Get existing queue items to avoid duplicates/status check
        en_cola = NotificacionEncolada.objects.filter(
            turno__in=turnos
        ).values('turno_id', 'tipo', 'estado')
        memo_cola = {(c['turno_id'], c['tipo']): c['estado'] for c in en_cola}

        proyeccion = []
        
        for turno in turnos:
            if not turno.fecha or not turno.hora:
                continue

            try:
                turno_dt = timezone.make_aware(datetime.combine(turno.fecha, turno.hora))
            except:
                turno_dt = datetime.combine(turno.fecha, turno.hora)
            
            # --- Regla 1: Anticipado ---
            if config.activar_anticipado:
                fecha_notif = turno_dt - timedelta(days=config.dias_antes)
                local_notif_date = timezone.localdate(fecha_notif)
                
                # Check strict range [start, end)
                if start_date <= local_notif_date < end_date:
                    estado_real = memo_cola.get((turno.id, 'anticipado'))
                    ya_procesado = estado_real in ['enviado', 'procesando', 'cancelado']
                    
                    if not ya_procesado:
                         # Calculate days remaining for display
                        days_diff = (local_notif_date - local_today).days
                        
                        proyeccion.append({
                            'turno': turno,
                            'tipo': 'anticipado',
                            'tipo_id': f"{turno.id}:anticipado", # Critical for form submission
                            'tipo_display': 'Recordatorio Anticipado',
                            'fecha_programada': fecha_notif,
                            'responsable': turno.responsable,
                            'ya_procesado': False, 
                            'estado_actual': estado_real or 'virtual',
                            'missing_email': not bool(turno.responsable.email),
                            'days_diff': days_diff,
                            'sort_date': fecha_notif
                        })

            # --- Regla 2: Jornada ---
            if config.activar_jornada:
                fecha_notif = turno_dt - timedelta(minutes=config.minutos_antes_jornada)
                local_notif_date = timezone.localdate(fecha_notif)
                
                # Check strict range [start, end)
                if start_date <= local_notif_date < end_date:
                    estado_real = memo_cola.get((turno.id, 'jornada'))
                    ya_procesado = estado_real in ['enviado', 'procesando', 'cancelado']
                    
                    if not ya_procesado and fecha_notif > (now - timedelta(hours=2)): # Hide if strictly passed
                        # Calculate days remaining for display
                        days_diff = (local_notif_date - local_today).days
                        
                        proyeccion.append({
                            'turno': turno,
                            'tipo': 'jornada',
                            'tipo_id': f"{turno.id}:jornada", # Critical for form submission
                            'tipo_display': 'Día del Turno',
                            'fecha_programada': fecha_notif,
                            'responsable': turno.responsable,
                            'ya_procesado': False,
                            'estado_actual': estado_real or 'virtual',
                            'missing_email': not bool(turno.responsable.email),
                            'days_diff': days_diff,
                            'sort_date': fecha_notif
                        })
        
        # Sort by the actual notification date
        proyeccion.sort(key=lambda x: x['sort_date'])
        return proyeccion


    def _procesar_envio_individual(notification_id):
        """
        Método worker para ser ejecutado en hilo separado.
        Realiza el envío de UNA notificación y gestiona su estado.
        """
        # Delay aleatorio anti-spam (0.3s - 1.0s)
        import random
        time.sleep(random.uniform(0.3, 1.0))
        
        # Obtener nueva conexión para este hilo (Thread-Safe)
        connection = get_connection()
        try:
            connection.open()
        except:
            pass # Si falla open aquí, fallará en send y capturaremos la excepción abajo

        try:
            # Re-obtener objeto fresco de la BD
            try:
                item = NotificacionEncolada.objects.get(id=notification_id)
            except NotificacionEncolada.DoesNotExist:
                return False # Ya no existe
                
            turno = item.turno
            if not turno or not turno.responsable or not turno.responsable.email:
                raise Exception("El responsable no tiene un correo electrónico configurado.")

            if not settings.DEFAULT_FROM_EMAIL:
                raise Exception("El sistema no tiene configurado el correo emisor (DEFAULT_FROM_EMAIL).")

            config = ConfiguracionNotificacion.get_solo()
            subject, body_text = NotificationService._preparar_contenido(item, config)

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
                connection=connection # Usar la conexión de este hilo
            )
            msg.attach_alternative(html_message, "text/html")

            # Adjuntar imágenes (Header)
            header_img_path = os.path.join(settings.BASE_DIR, 'core', 'static', 'img', 'mujeru.jpg')
            if os.path.exists(header_img_path):
                with open(header_img_path, 'rb') as f:
                    h_img = MIMEImage(f.read())
                    h_img.add_header('Content-ID', '<header_image>')
                    msg.attach(h_img)

            # Adjuntar Logo
            logo_path = os.path.join(settings.BASE_DIR, 'unemi.png')
            if os.path.exists(logo_path):
                with open(logo_path, 'rb') as f:
                    l_img = MIMEImage(f.read())
                    l_img.add_header('Content-ID', '<unemi_logo>')
                    msg.attach(l_img)

            msg.send()

            # ÉXITO
            item.estado = 'enviado'
            item.intentos += 1
            item.ultimo_error = ""
            item.save()
            
            # Auditoría
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
            connection.close()
            return True

        except Exception as e:
            # MANEJO DE ERROR
            connection.close()
            try:
                # Recargar por si acaso
                item = NotificacionEncolada.objects.get(id=notification_id)
                item.intentos += 1
                item.ultimo_error = str(e)
                
                # Política de Reintentos (Max 3)
                if item.intentos >= item.max_intentos:
                    item.estado = 'fallido'
                else:
                    item.estado = 'error_temporal' # Permite reintento en siguiente ciclo
                item.save()
                
                HistorialEnvio.objects.create(
                    notificacion=item,
                    turno=item.turno,
                    tipo=item.tipo,
                    intento_n=item.intentos,
                    estado='fallido' if item.estado == 'fallido' else 'reintento',
                    destinatario=item.turno.responsable.email if item.turno and item.turno.responsable else "Desconocido",
                    asunto=f"Error: {item.get_tipo_display()}",
                    error_log=str(e)
                )
            except:
                pass # Si falla al guardar error, no podemos hacer mucho más
            return False

    @staticmethod
    def ejecutar_vigilancia(specific_ids=None):
        """
        EL PROCESADOR DE COLA (PARALELO).
        Usa ThreadPoolExecutor para enviar notificaciones concurrentes.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        now = timezone.now()
        
        # 1. Seleccionar candidatos
        if specific_ids:
            cola = NotificacionEncolada.objects.filter(
                id__in=specific_ids,
                estado__in=['pendiente', 'error_temporal', 'enviado', 'fallido', 'cancelado']
            )
        else:
            cola = NotificacionEncolada.objects.filter(
                estado__in=['pendiente', 'error_temporal'],
                fecha_programada__lte=now
            )
            
        if not cola.exists():
            return 0, 0

        # 2. Marcar como 'procesando' (Bloqueo lógico)
        ids_to_process = []
        for item in cola:
            updated = NotificacionEncolada.objects.filter(id=item.id, estado=item.estado).update(estado='procesando')
            if updated > 0:
                ids_to_process.append(item.id)
        
        if not ids_to_process:
            return 0, 0

        # 3. Ejecución Paralela
        enviados = 0
        errores = 0
        MAX_WORKERS = 8 # Límite recomendado
        
        print(f"Iniciando procesamiento paralelo de {len(ids_to_process)} notificaciones con {MAX_WORKERS} workers...")
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Mapear futuros
            future_to_id = {executor.submit(NotificationService._procesar_envio_individual, nid): nid for nid in ids_to_process}
            
            for future in as_completed(future_to_id):
                nid = future_to_id[future]
                try:
                   is_success = future.result()
                   if is_success:
                       enviados += 1
                   else:
                       errores += 1
                except Exception as exc:
                    print(f'Notificación {nid} generó una excepción no manejada: {exc}')
                    errores += 1

        # Auditoría Batch (Resumen)
        if enviados > 0 or errores > 0:
            from .models import AuditLogNotificaciones
            AuditLogNotificaciones.objects.create(
                notificacion=None, # Log general
                accion="Ejecución Masiva (Paralela)",
                detalles=f"Procesados: {len(ids_to_process)}. Exitosos: {enviados}. Fallidos/Reintentos: {errores}."
            )
            
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
