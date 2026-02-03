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
        # Escaneamos con un margen amplio para capturar turnos manuales lejanos
        # que podrían disparar notificaciones pronto (ej: días_antes=30)
        local_today = timezone.localdate(now)
        lookahead_sync = max(config.dias_antes, 7) + 7
        end_date = local_today + timedelta(days=lookahead_sync)
        
        turnos = Turno.objects.filter(
            fecha__gte=local_today - timedelta(days=1),
            fecha__lte=end_date,
            estado__in=['pendiente', 'asignado']
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
                # Permitir planificar si es futuro o del pasado reciente (para que el Radar lo muestre)
                obj, created = NotificacionEncolada.objects.get_or_create(
                    turno=turno,
                    tipo='anticipado',
                    defaults={'fecha_programada': prog}
                )
                if created: creadas += 1

            # Regla 2: Día del Turno (ELIMINADA por redundancia)
        
        if creadas == 0 and not config.activar_anticipado:
            print("DEBUG: Sincronización terminada - Regla de notificación desactivada.")

        return creadas

    @staticmethod
    def fix_turno_sync():
        """
        Retroactive Sync: Busca notificaciones enviadas y asegura que el Turno refleje ese estado.
        También limpia estados 'procesando' que hayan quedado huérfanos.
        """
        from .models import NotificacionEncolada
        from core.models import Turno
        from django.utils import timezone
        
        # 1. Recuperar items 'procesando' (huérfanos de hilos que crashearon o reiniciaron)
        # Si llevan más de 1 hora en 'procesando', los volvemos a 'pendiente'
        una_hora_atras = timezone.now() - timedelta(hours=1)
        stuck = NotificacionEncolada.objects.filter(estado='procesando', fecha_programada__lte=una_hora_atras)
        stuck_count = stuck.update(estado='pendiente')
        
        # 2. Sincronizar Turnos basándonos en notificaciones enviadas
        sent_notifs = NotificacionEncolada.objects.filter(estado='enviado').select_related('turno')
        synced = 0
        for n in sent_notifs:
            if n.turno and not n.turno.notificacion_enviada:
                n.turno.notificacion_enviada = True
                n.turno.notificacion_error = False
                n.turno.save()
                synced += 1
        
        return synced, stuck_count
    @staticmethod
    def calcular_proyeccion(dias=7, offset=0, overrides=None):
        """
        Calcula qué notificaciones corresponden a los turnos en el rango [offset, offset+dias].
        Permite overrides de configuración para previsualizar cambios en el Wizard.
        """
        config = ConfiguracionNotificacion.get_solo()
        
        if overrides:
            if 'dias_antes' in overrides: config.dias_antes = int(overrides['dias_antes'])
            if 'minutos_antes_jornada' in overrides: config.minutos_antes_jornada = int(overrides['minutos_antes_jornada'])
            if 'activar_anticipado' in overrides: config.activar_anticipado = overrides['activar_anticipado']
            if 'activar_jornada' in overrides: config.activar_jornada = overrides['activar_jornada']

        now = timezone.now()
        local_today = timezone.localdate(now)
        
        # Define window based on Notification Date
        # AHORA BUSCAMOS DESDE HACE 30 DÍAS (p/atrapar lo no enviado) solo si offset es 0
        radar_start = local_today - timedelta(days=30) if offset == 0 else (local_today + timedelta(days=offset))
        end_date = (local_today + timedelta(days=offset)) + timedelta(days=dias)
        
        # 0. Sincronizar cola antes de calcular para asegurar que lo manual aparezca
        NotificationService.sincronizar_cola()

        # 1. Fetch Candidates
        lookahead = max(config.dias_antes, 1) + 15 
        
        turnos = Turno.objects.filter(
            fecha__gte=radar_start - timedelta(days=lookahead), 
            fecha__lte=end_date + timedelta(days=lookahead), 
            estado__in=['pendiente', 'asignado', 'en_proceso']
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
                if radar_start <= local_notif_date < end_date:
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

            # --- Regla 2: Jornada (ELIMINADA) ---
            # Se elimina por redundancia con envío manual.
        
        # Sort by the actual notification date
        proyeccion.sort(key=lambda x: x['sort_date'])
        return proyeccion


    def _procesar_envio_individual(notification_id):
        """
        Método worker para ser ejecutado en hilo separado.
        Realiza el envío de UNA notificación y gestiona su estado.
        """
        # Delay aleatorio anti-spam optimizado (0.1s - 0.3s)
        import random
        time.sleep(random.uniform(0.1, 0.3))
        
        # Obtener nueva conexión para este hilo (Thread-Safe)
        connection = get_connection()
        max_connection_attempts = 3
        for attempt in range(max_connection_attempts):
            try:
                connection.open()
                break
            except Exception as conn_err:
                if attempt == max_connection_attempts - 1:
                    print(f"Error abriendo conexión SMTP tras {max_connection_attempts} intentos: {conn_err}")
                else:
                    time.sleep(0.5 * (attempt + 1))  # Backoff: 0.5s, 1s, 1.5s

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
            logo_path = os.path.join(settings.BASE_DIR, 'core', 'static', 'img', 'unemi.png')
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

            if item.turno:
                item.turno.notificacion_enviada = True
                item.turno.notificacion_error = False
                item.turno.ultimo_envio = timezone.now()
                item.turno.save(update_fields=['notificacion_enviada', 'notificacion_error', 'ultimo_envio'])
            
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
            # MANEJO DE ERROR MEJORADO
            error_type = type(e).__name__
            print(f"Error enviando notificación {notification_id}: {error_type} - {str(e)[:100]}")
            
            try:
                connection.close()
            except:
                pass
            
            try:
                # Recargar por si acaso
                item = NotificacionEncolada.objects.get(id=notification_id)
                item.intentos += 1
                item.ultimo_error = f"{error_type}: {str(e)[:200]}"
                
                # Política de Reintentos (Max 3)
                if item.intentos >= item.max_intentos:
                    item.estado = 'fallido'
                    print(f"  → Marcado como FALLIDO tras {item.intentos} intentos")
                else:
                    item.estado = 'error_temporal' # Permite reintento en siguiente ciclo
                    print(f"  → Marcado como ERROR_TEMPORAL (intento {item.intentos}/{item.max_intentos})")
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
                if item.turno:
                    item.turno.notificacion_error = True
                    item.turno.save(update_fields=['notificacion_error'])
            except Exception as save_err:
                print(f"Error guardando estado de fallo para notificación {notification_id}: {save_err}")
            return False

    @staticmethod
    def ejecutar_vigilancia(specific_ids=None):
        """
        EL PROCESADOR DE COLA (SECUENCIAL CON CONEXIÓN REUTILIZABLE).
        Procesa notificaciones secuencialmente para evitar rechazo del servidor SMTP.
        """
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

        # 3. Ejecución PARALELA SÍNCRONA (Upgrade)
        # Usamos la misma lógica multithread que el generador para máxima velocidad.
        enviados = 0
        errores = 0
        
        import time as time_module
        import concurrent.futures
        start_time = time_module.time()
        
        BATCH_SIZE = 1 # Reducido para mayor feedback en Step 1
        MAX_WORKERS = 8
        total = len(ids_to_process)
        print(f"🚀 Iniciando envío masivo SÍNCRONO de {total} correos (8 threads)...")

        # Caché de imágenes
        header_cache = None
        logo_cache = None
        try:
            h_path = os.path.join(settings.BASE_DIR, 'core', 'static', 'img', 'mujeru.jpg')
            if os.path.exists(h_path):
                with open(h_path, 'rb') as f: header_cache = f.read()
            l_path = os.path.join(settings.BASE_DIR, 'core', 'static', 'img', 'unemi.png')
            if os.path.exists(l_path):
                with open(l_path, 'rb') as f: logo_cache = f.read()
        except: pass

        # Batches
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
        batches = []
        for i in range(total_batches):
            s = i * BATCH_SIZE
            e = min((i + 1) * BATCH_SIZE, total)
            batches.append(ids_to_process[s:e])

        # Worker (Copia del generador pero sin status yield)
        def process_batch_thread_sync(batch_ids, batch_num):
            from django.db import connection as db_connection
            conn = get_connection(timeout=15)
            local_sent = 0
            local_errors = 0
            try:
                conn.open()
                msgs = []
                valid_objs = []
                for nid in batch_ids:
                    try:
                        m, o = NotificationService._preparar_mensaje_individual(
                            nid, conn, 
                            header_img_cache=header_cache, 
                            logo_img_cache=logo_cache
                        )
                        if m and o:
                            msgs.append(m)
                            valid_objs.append(o)
                        else: local_errors += 1
                    except: local_errors += 1
                if msgs:
                    try:
                        # Enviar de verdad (CRITICO)
                        if msgs:
                            conn.send_messages(msgs)

                        # Sync NOTIFICACIONES in batch
                        v_ids = [vo.id for vo in valid_objs]
                        if v_ids:
                             NotificacionEncolada.objects.filter(id__in=v_ids).update(
                                 estado='enviado',
                                 ultimo_error=''
                             )

                        # Sync Turnos in batch
                        turno_ids = [vo.turno.id for vo in valid_objs if vo.turno]
                        if turno_ids:
                            from core.models import Turno
                            Turno.objects.filter(id__in=turno_ids).update(
                                notificacion_enviada=True, 
                                notificacion_error=False,
                                ultimo_envio=timezone.now()
                            )
                        local_sent = len(msgs)
                        
                        # Auditoría Loop
                        for vo in valid_objs:
                            try:
                                HistorialEnvio.objects.create(
                                    notificacion=vo, turno=vo.turno, tipo=vo.tipo, estado='enviado',
                                    destinatario=vo.turno.responsable.email, asunto="Notif Masiva Sync"
                                )
                            except: pass
                    except Exception as send_err:
                        print(f"Error Thread {batch_num}: {send_err}")
                        local_errors += len(msgs)
                        v_ids = [x.id for x in valid_objs]
                        NotificacionEncolada.objects.filter(id__in=v_ids).update(
                            estado='error_temporal', ultimo_error=f"ThreadErr: {str(send_err)[:100]}"
                        )
                        # Sync Turnos Error
                        error_turno_ids = [vo.turno.id for vo in valid_objs if vo.turno]
                        if error_turno_ids:
                            from core.models import Turno
                            Turno.objects.filter(id__in=error_turno_ids).update(notificacion_error=True)
            except Exception as conn_err:
                local_errors += len(batch_ids)
                print(f"Error Conn Thread {batch_num}: {conn_err}")
            finally:
                try: conn.close()
                except: pass
                db_connection.close() # Importante para evitar fugas en multithreading
            return (local_sent, local_errors)

        # Ejecutar Pool
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(process_batch_thread_sync, b, idx+1) for idx, b in enumerate(batches)]
            for future in concurrent.futures.as_completed(futures):
                try:
                    s, e = future.result()
                    enviados += s
                    errores += e
                except Exception as exc:
                    print(f"Excepción hilo sync: {exc}")
                    # No sumamos errores aqui porque se manejan dentro, pero por seguridad
                    pass

        elapsed_time = time_module.time() - start_time
        print(f"DEBUG: Ejecución terminada. Enviados: {enviados}, Errores: {errores}, Tiempo: {elapsed_time:.2f}s")

        # Auditoría Batch (Resumen)
        NotificationService._log_batch_audit(len(ids_to_process), enviados, errores, elapsed_time)
            
        return enviados, errores

    @staticmethod
    def ejecutar_vigilancia_generator(specific_ids=None):
        """
        GENERADOR DE STREAMING PARA UI.
        Igual que ejecutar_vigilancia pero con 'yield' para reportar progreso en tiempo real.
        """
        import time as time_module
        import json
        
        now = timezone.now()
        
        # 1. Seleccionar candidatos
        if specific_ids:
            # Si es manual, permitimos reintentar CUALQUIER estado excepto 'procesando' por otro worker (aunque asumimos single threading aqui)
            # Incluso si está 'enviado', lo permitimos si el usuario forzó el reenvío.
            cola = NotificacionEncolada.objects.filter(
                id__in=specific_ids
            )
        else:
            cola = NotificacionEncolada.objects.filter(
                estado__in=['pendiente', 'error_temporal'],
                fecha_programada__lte=now
            )
            
        if not cola.exists():
            yield json.dumps({'progress': 0, 'status': f'No se encontraron notificaciones (IDs: {specific_ids})'})
            return

        # 2. Marcar como 'procesando'
        ids_to_process = []
        for item in cola:
            # Forzamos el estado a procesando sin importar el estado anterior si es selección manual
            # Para cron automático, mantenemos el check de concurrencia
            if specific_ids:
                updated = NotificacionEncolada.objects.filter(id=item.id).update(estado='procesando')
            else:
                updated = NotificacionEncolada.objects.filter(id=item.id, estado=item.estado).update(estado='procesando')
            
            if updated > 0:
                ids_to_process.append(item.id)
        
        if not ids_to_process:
             yield json.dumps({'progress': 0, 'status': 'No se pudieron procesar notificaciones (IDs vacíos).'})
             return
        
        total = len(ids_to_process)
        yield json.dumps({'progress': 1, 'status': f'Preparando envío de {total} correos...', 'total': total, 'sent': 0, 'errors': 0})

        # 3. Ejecución SECUENCIAL
        enviados = 0
        errores = 0
        
        start_time = time_module.time()
        
        # Conexión persistente
        yield json.dumps({'progress': 2, 'status': 'Conectando con el servidor de correo...', 'total': total})
        
        connection = get_connection()
        connection_open = False
        
        try:
            connection.open()
            connection_open = True
            yield json.dumps({'progress': 5, 'status': 'Conexión establecida. Enviando...', 'total': total})
        except Exception as conn_err:
            yield json.dumps({'progress': 0, 'status': f'Error de Conexión SMTP: {str(conn_err)}', 'error': True})
            NotificacionEncolada.objects.filter(id__in=ids_to_process).update(
                estado='error_temporal',
                ultimo_error=f"No se pudo establecer conexión SMTP: {str(conn_err)[:200]}"
            )
            return
        
        
        # Pre-cargar imágenes para Caché (Evita 160 lecturas de disco)
        header_cache = None
        logo_cache = None
        try:
            h_path = os.path.join(settings.BASE_DIR, 'core', 'static', 'img', 'mujeru.jpg')
            if os.path.exists(h_path):
                with open(h_path, 'rb') as f: header_cache = f.read()
            
            l_path = os.path.join(settings.BASE_DIR, 'core', 'static', 'img', 'unemi.png')
            if os.path.exists(l_path):
                with open(l_path, 'rb') as f: logo_cache = f.read()
        except:
            pass

        
        # Procesamiento en HILOS PARALELOS (Multithreading) EXTREMO
        # 8 Hilos x 10 emails = hasta 80 emails en vuelo simultáneo.
        # Esto debería ser casi instantáneo para volúmenes medianos.
        import concurrent.futures
        
        BATCH_SIZE = 1 
        MAX_WORKERS = 8
        
        # Dividir en lotes totales
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
        batches = []
        for i in range(total_batches):
            s = i * BATCH_SIZE
            e = min((i + 1) * BATCH_SIZE, total)
            batches.append(ids_to_process[s:e])
            
        yield json.dumps({'progress': 5, 'status': '🚀 Iniciando envío masivo...', 'total': total})
        
        # Cerrar conexión principal de prueba, los hilos abrirán las suyas
        if connection_open:
            try: connection.close()
            except: pass

        # Función Worker para cada hilo
        def process_batch_thread(batch_ids, batch_num):
            from django.db import connection as db_connection
            conn = get_connection(timeout=15)
            local_sent = 0
            local_errors = 0
            try:
                conn.open()
                msgs = []
                valid_objs = []
                
                # Preparar
                for nid in batch_ids:
                    try:
                        m, o = NotificationService._preparar_mensaje_individual(
                            nid, conn, 
                            header_img_cache=header_cache, 
                            logo_img_cache=logo_cache
                        )
                        if m and o:
                            msgs.append(m)
                            valid_objs.append(o)
                        else:
                            local_errors += 1
                    except:
                        local_errors += 1
                
                # Enviar
                # Enviar de verdad (CRITICO)
                if msgs:
                    try:
                        conn.send_messages(msgs)
                        
                        # Sync NOTIFICACIONES in batch
                        v_ids = [vo.id for vo in valid_objs]
                        if v_ids:
                             NotificacionEncolada.objects.filter(id__in=v_ids).update(
                                 estado='enviado',
                                 ultimo_error=''
                             )

                        # Sync Turnos in batch
                        turno_ids = [vo.turno.id for vo in valid_objs if vo.turno]
                        if turno_ids:
                            from core.models import Turno
                            Turno.objects.filter(id__in=turno_ids).update(
                                notificacion_enviada=True, 
                                notificacion_error=False,
                                ultimo_envio=timezone.now()
                            )
                        local_sent = len(msgs)
                        
                        # Auditoría Loop
                        for vo in valid_objs:
                            try:
                                HistorialEnvio.objects.create(
                                    notificacion=vo, turno=vo.turno, tipo=vo.tipo, estado='enviado',
                                    destinatario=vo.turno.responsable.email, asunto="Notif Masiva Thread"
                                )
                            except: pass
                            
                    except Exception as send_err:
                        print(f"Error Thread Batch {batch_num}: {send_err}")
                        local_errors += len(msgs)
                        v_ids = [x.id for x in valid_objs]
                        NotificacionEncolada.objects.filter(id__in=v_ids).update(
                            estado='error_temporal', ultimo_error=f"ThreadErr: {str(send_err)[:100]}"
                        )
                        # Sync Turnos Error
                        error_turno_ids = [vo.turno.id for vo in valid_objs if vo.turno]
                        if error_turno_ids:
                            from core.models import Turno
                            Turno.objects.filter(id__in=error_turno_ids).update(notificacion_error=True)
            except Exception as conn_err:
                local_errors += len(batch_ids)
                print(f"Error Conexión Thread {batch_num}: {conn_err}")
            finally:
                try: conn.close()
                except: pass
                db_connection.close()
                
            return (local_sent, local_errors)

        # Ejecutar Pool
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_batch = {executor.submit(process_batch_thread, b, idx+1): idx for idx, b in enumerate(batches)}
            
            completed_batches = 0
            for future in concurrent.futures.as_completed(future_to_batch):
                batch_index = future_to_batch[future]
                completed_batches += 1
                
                try:
                    b_sent, b_errors = future.result()
                    enviados += b_sent
                    errores += b_errors
                except Exception as exc:
                    print(f"Excepción en hilo: {exc}")
                    errores += len(batches[batch_index])
                
                # Progreso
                pct = 10 + int((completed_batches / total_batches) * 85)
                yield json.dumps({
                    'progress': pct,
                    'status': "✉️ Enviando notificaciones...",
                    'sent': enviados,
                    'errors': errores,
                    'total': total
                })

        # Finalizar
        elapsed_time = time_module.time() - start_time
        
        # Cerrar conexión
        if connection_open:
            try:
                connection.close()
            except:
                pass
        
        elapsed_time = time_module.time() - start_time
        
        # Auditoría Batch
        if enviados > 0 or errores > 0:
            NotificationService._log_batch_audit(total, enviados, errores, elapsed_time)

        yield json.dumps({
            'progress': 100,
            'status': '¡Proceso completado!',
            'sent': enviados,
            'errors': errores,
            'total': total,
            'completed': True
        })

    @staticmethod
    def _log_batch_audit(total, sent, errors, time):
        from .models import AuditLogNotificaciones
        AuditLogNotificaciones.objects.create(
            notificacion=None,
            accion="Ejecución Masiva (Streaming)",
            detalles=f"Procesados: {total}. Exitosos: {sent}. Fallidos/Reintentos: {errors}. Tiempo: {time:.2f}s"
        )


    @staticmethod
    def _preparar_mensaje_individual(notification_id, connection, header_img_cache=None, logo_img_cache=None):
        """
        Helper para Batch Sending: Prepara el objeto EmailMultiAlternatives sin enviarlo.
        Retorna (msg, item) o (None, None) si error.
        Acepta caches de imagen (bytes) para evitar I/O repetitivo.
        """
        try:
            item = NotificacionEncolada.objects.get(id=notification_id)
            turno = item.turno
            if not turno or not turno.responsable or not turno.responsable.email:
                raise Exception("Responsable sin email")

            if not settings.DEFAULT_FROM_EMAIL:
                raise Exception("Falta DEFAULT_FROM_EMAIL")

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
                connection=connection  # Usar conexión del batch
            )
            msg.attach_alternative(html_message, "text/html")

            # Adjuntos Optimados (Memoria)
            # Header
            if header_img_cache:
                 h_img = MIMEImage(header_img_cache)
                 h_img.add_header('Content-ID', '<header_image>')
                 msg.attach(h_img)
            else:
                # Fallback Disco (Solo si no hay cache)
                header_img_path = os.path.join(settings.BASE_DIR, 'core', 'static', 'img', 'mujeru.jpg')
                if os.path.exists(header_img_path):
                    with open(header_img_path, 'rb') as f:
                        h_img = MIMEImage(f.read())
                        h_img.add_header('Content-ID', '<header_image>')
                        msg.attach(h_img)

            # Logo
            if logo_img_cache:
                 l_img = MIMEImage(logo_img_cache)
                 l_img.add_header('Content-ID', '<unemi_logo>')
                 msg.attach(l_img)
            else:
                # Fallback Disco
                logo_path = os.path.join(settings.BASE_DIR, 'core', 'static', 'img', 'unemi.png')
                if os.path.exists(logo_path):
                    with open(logo_path, 'rb') as f:
                        l_img = MIMEImage(f.read())
                        l_img.add_header('Content-ID', '<unemi_logo>')
                        msg.attach(l_img)
            
            return msg, item

        except Exception as e:
            # Si falla la preparación, intentamos registrar el error en el item si es posible
            print(f"Error preparando msg {notification_id}: {e}")
            try:
                it = NotificacionEncolada.objects.get(id=notification_id)
                it.estado = 'error_temporal'
                it.ultimo_error = str(e)[:200]
                it.save()
            except: pass
            return None, None

    @staticmethod
    def _enviar_con_conexion_existente(notification_id, connection):
        """
        Envía UNA notificación usando una conexión SMTP ya establecida.
        Retorna True si tuvo éxito, False si falló.
        """
        try:
            # Re-obtener objeto fresco de la BD
            try:
                item = NotificacionEncolada.objects.get(id=notification_id)
            except NotificacionEncolada.DoesNotExist:
                return False
                
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
                connection=connection  # Usar la conexión compartida
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
            logo_path = os.path.join(settings.BASE_DIR, 'core', 'static', 'img', 'unemi.png')
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
            
            # Sync Turno
            if turno:
                from django.utils import timezone
                turno.notificacion_enviada = True
                turno.notificacion_error = False
                turno.ultimo_envio = timezone.now()
                turno.save()
            
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
            return True

        except Exception as e:
            # MANEJO DE ERROR
            error_type = type(e).__name__
            
            try:
                item = NotificacionEncolada.objects.get(id=notification_id)
                item.intentos += 1
                item.ultimo_error = f"{error_type}: {str(e)[:200]}"
                
                # Política de Reintentos (Max 3)
                if item.intentos >= item.max_intentos:
                    item.estado = 'fallido'
                    if item.turno:
                        item.turno.notificacion_error = True
                        item.turno.save()
                else:
                    item.estado = 'error_temporal'
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
            except Exception as save_err:
                print(f"Error guardando estado de fallo para notificación {notification_id}: {save_err}")
            
            return False

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
            logo_path = os.path.join(settings.BASE_DIR, 'core', 'static', 'img', 'unemi.png')
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
            
            # Sync Turno (CRITICO para el emoticón)
            if turno:
                from django.utils import timezone
                turno.notificacion_enviada = True
                turno.notificacion_error = False
                turno.ultimo_envio = timezone.now()
                turno.save()
            
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
            
            # Sync Turno Error
            if item.turno:
                item.turno.notificacion_error = True
                item.turno.save()
            
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
        elif turno.equipos.exists():
            evento_desc = turno.equipos.first().descripcion
        
        if not evento_desc or str(evento_desc).lower() == 'nan':
            evento_desc = config.actividad_general

        # Lógica dinámica avanzada
        equipos = turno.equipos.all()
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
