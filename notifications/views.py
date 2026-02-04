from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.utils import timezone
from datetime import datetime, timedelta
import json
from .models import ConfiguracionNotificacion, HistorialEnvio, NotificacionEncolada, AuditLogNotificaciones
from .services import NotificationService
from core.models import Turno, Equipo, Responsable, ConfiguracionCronograma

def dashboard(request):
    """
    Vista principal del Sistema Manual de Notificaciones.
    Dashboard con métricas y gestión de notificaciones manuales.
    """
    config = ConfiguracionNotificacion.get_solo()
    
    # Calcular métricas para el nuevo dashboard
    metrics = {
        'total_volumen': NotificacionEncolada.objects.count(),
        'total_exitosas': HistorialEnvio.objects.filter(estado='enviado').count(),
        'total_errores': HistorialEnvio.objects.filter(estado='fallido').count(),
        'total_pendientes': NotificacionEncolada.objects.filter(estado__in=['pendiente', 'error_temporal']).count(),
        'total_equipos': Equipo.objects.count(),
    }
    

    
    # --- DATOS PARA PREVISUALIZACIÓN REALISTA ---
    # Buscamos un responsable real con equipos para el ejemplo
    ejemplo_data = {
        'funcionario': 'Nombre del Funcionario',
        'equipos_lista': '• Equipo marca Modelo (Cód: 000)',
        'fecha_turno': '01 de Enero del 2026',
        'hora': '08:00 AM',
        'marca': 'Marca',
        'modelo': 'Modelo',
        'duracion': 30
    }
    
    primer_turno = Turno.objects.exclude(estado='cancelado').select_related('responsable').first()
    if primer_turno:
        resp = primer_turno.responsable
        equipos = primer_turno.equipos.all()
        if equipos.exists():
            lista = "\\n".join([f"• {e.marca} {e.modelo} (Cód: {e.codigo or 'N/A'})" for e in equipos])
            marca = equipos.first().marca
            modelo = equipos.first().modelo
        else:
            lista = "Equipo informático general"
            marca = "Hardware"
            modelo = "General"
            
        # Obtener duración real
        crono = ConfiguracionCronograma.objects.first()
        duracion = crono.duracion_turno if crono else 30
            
        ejemplo_data.update({
            'funcionario': resp.nombre,
            'equipos_lista': lista,
            'fecha_turno': primer_turno.fecha.strftime("%d de %B del %Y") if primer_turno.fecha else "-",
            'hora': primer_turno.hora.strftime("%H:%M %p") if primer_turno.hora else "-",
            'marca': marca,
            'modelo': modelo,
            'duracion': duracion
        })
    
    # --- HISTORIAL (AUDITORÍA) CON PAGINACIÓN Y FILTROS ---
    historial_queryset = HistorialEnvio.objects.select_related(
        'turno', 
        'turno__responsable'
    ).order_by('-fecha_envio')

    # Filtros
    fecha_desde = request.GET.get('fecha_desde')
    fecha_hasta = request.GET.get('fecha_hasta')
    estado = request.GET.get('estado')
    search = request.GET.get('search')

    if fecha_desde and fecha_desde.strip():
        historial_queryset = historial_queryset.filter(turno__fecha__gte=fecha_desde)
    if fecha_hasta and fecha_hasta.strip():
        historial_queryset = historial_queryset.filter(turno__fecha__lte=fecha_hasta)
    if estado and estado.strip():
        historial_queryset = historial_queryset.filter(estado=estado)
    if search and search.strip():
        historial_queryset = historial_queryset.filter(
            Q(turno__responsable__nombre__icontains=search) | 
            Q(destinatario__icontains=search) |
            Q(asunto__icontains=search)
        )
    
    paginator_hist = Paginator(historial_queryset, 10) # 10 por página para no sobrecargar
    page_number_hist = request.GET.get('page')
    historial = paginator_hist.get_page(page_number_hist)

    # --- PROYECCIÓN (RADAR SEMANAL) ---
    proyeccion_list = NotificationService.calcular_proyeccion(7)
    paginator_proj = Paginator(proyeccion_list, 5) # 5 notificaciones por página (consistente con el historial)
    page_number_proj = request.GET.get('page_proj')
    proyeccion = paginator_proj.get_page(page_number_proj)
    
    # Determine Active Tab
    active_tab = 'dashboard'
    tab_param = request.GET.get('tab')
    if tab_param == 'history':
        active_tab = 'history'
    elif any(k in request.GET for k in ['fecha_desde', 'fecha_hasta', 'estado', 'search', 'page']):
         active_tab = 'history'
    
    context = {
        'config': config,
        'metrics': metrics,
        'active_tab': active_tab,

        'historial': historial, # Usamos el objeto paginado
        'ejemplo_real': ejemplo_data,
        'proyeccion': proyeccion, # Ahora es un Page object
    }
    return render(request, 'notifications/dashboard.html', context)

def sincronizar_cola_view(request):
    """
    Controlador para forzar la sincronización de la cola con los turnos.
    """
    creadas = NotificationService.sincronizar_cola()
    synced, stuck = NotificationService.fix_turno_sync()
    
    # Si es AJAX (fetch), retornamos JSON
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1':
        return JsonResponse({
            'status': 'ok',
            'creadas': creadas,
            'synced': synced,
            'stuck_fixed': stuck,
            'message': f"Programadas: {creadas}, Sincronizadas: {synced}, Huérfanas: {stuck}"
        })

    msg = f"Se han programado {creadas} nuevas notificaciones. "
    if synced > 0:
        msg += f"Se han sincronizado {synced} estados de turnos."
    
    messages.success(request, msg)
    return redirect('notifications_dashboard')

def ejecutar_envios(request):
    """
    Endpoint para despertar al Procesador de Cola manualmente.
    """
    if request.method == 'POST':
        enviados, errores = NotificationService.ejecutar_vigilancia()
        total = enviados + errores
        if total == 0:
             messages.info(request, "No hay notificaciones pendientes para enviar. Todo está al día.")
        elif errores > 0:
            messages.warning(request, f"Se enviaron {enviados} correctamente, pero {errores} fallaron. Sugerencia: Revisa los detalles en la Bitácora de Auditoría abajo para corregir correos o reintentar individualmente.")
        else:
            messages.success(request, f"¡Excelente! Se enviaron {enviados} notificaciones exitosamente.")
    
    return redirect('notifications_dashboard')




@csrf_exempt
def stream_envio_masivo(request):
    """
    Endpoint para streaming de progreso usando Server-Sent Events (SSE) simulado (NDJSON).
    Recibe IDs de proyección (turno_id:tipo) o IDs de cola (int). 
    Si son de proyección, primero crea las notificaciones.
    """
    ids_raw = request.GET.get('ids', '')
    if not ids_raw:
        from django.http import JsonResponse
        return JsonResponse({'error': 'No IDs provided'}, status=400)

    # Detectar el formato de los IDs: '123:tipo' (proyección) vs '456' (cola)
    raw_list = ids_raw.split(',')
    
    final_queue_ids = []
    
    # Lógica de Generación "Just Code" (replicada de generar_desde_proyeccion simplificada)
    
    
    # Separar IDs simples (cola) de complejos (proyección)
    projection_items = [x for x in raw_list if ':' in x]
    queue_items = [int(x) for x in raw_list if ':' not in x and x.isdigit()]
    
    # Procesar items de proyección -> Crear Notificaciones Encoladas
    if projection_items:
        config = ConfiguracionNotificacion.get_solo()
        for item_str in projection_items:
            try:
                t_id, tipo = item_str.split(':')
                turno = Turno.objects.get(id=t_id)
                
                # Buscar si existe o crear nueva
                notif, created = NotificacionEncolada.objects.get_or_create(
                    turno=turno,
                    tipo=tipo,
                    defaults={
                        'fecha_programada': timezone.now(), # Inmediata
                        'estado': 'pendiente'
                    }
                )
                if not created and notif.estado in ['enviado', 'cancelado']:
                    # Si ya existía y estaba finalizada, y el usuario la pide de nuevo -> Reset
                    notif.estado = 'pendiente'
                    notif.fecha_programada = timezone.now()
                    notif.intentos = 0
                    notif.save()
                    
                final_queue_ids.append(notif.id)
            except Exception as e:
                print(f"Error generando notificación para {item_str}: {e}")

    # Agregar los IDs que ya eran de cola
    final_queue_ids.extend(queue_items)
    
    # Remover duplicados y preparar para envío
    final_queue_ids = list(set(final_queue_ids))

    from django.http import StreamingHttpResponse
    
    def event_stream():
        if not final_queue_ids:
             import json
             yield json.dumps({'progress': 0, 'status': 'No se pudieron generar notificaciones válidas.', 'total': 0}) + "\n"
             return

        # Generador que consume el servicio con los IDs reales de la cola
        for event_json in NotificationService.ejecutar_vigilancia_generator(final_queue_ids):
            yield event_json + "\n"
            
    response = StreamingHttpResponse(event_stream(), content_type='application/x-ndjson')
    response['Cache-Control'] = 'no-cache'
    return response


def generar_desde_proyeccion(request):
    """
    Toma selecciones del Radar de Proyección y las convierte en registros de la COLA.
    Ahora también actualiza la configuración base si se envía desde el Wizard.
    """
    if request.method == 'POST':
        # --- ACTUALIZAR CONFIGURACIÓN SOLO SI VIENE DEL WIZARD ---
        config = ConfiguracionNotificacion.get_solo()
        
        if request.POST.get('wizard_config_update') == '1':
            asunto = request.POST.get('asunto_template')
            cuerpo = request.POST.get('cuerpo_template')
            activar_ant = request.POST.get('activar_anticipado') == 'on'
            dias_antes = request.POST.get('dias_antes')

            if asunto: config.asunto_template = asunto
            if cuerpo: config.cuerpo_template = cuerpo
            
            config.activar_anticipado = activar_ant
            try:
                if dias_antes: config.dias_antes = int(dias_antes)
            except: pass

            # La regla 'Jornada/Hoy' ha sido eliminada por redundancia.
            config.activar_jornada = False 
            
            config.save()
            print("DEBUG: Configuración actualizada desde el Wizard (Regla Hoy desactivada).")
        else:
            print("DEBUG: Saltando actualización de config (envío desde Radar).")

        items = request.POST.getlist('proyeccion_items')
        print(f"DEBUG: Items recibidos ({len(items)}): {items}")
        ids_a_procesar = []
        creadas = 0
        ya_estaban = 0
        
        for val in items:
            try:
                if not ':' in val:
                    print(f"DEBUG: Valor inválido (sin ':'): {val}")
                    continue
                    
                t_id, tipo = val.split(':')
                turno = get_object_or_404(Turno, id=t_id)
                
                try:
                    dt = timezone.make_aware(datetime.combine(turno.fecha, turno.hora))
                except:
                    dt = datetime.combine(turno.fecha, turno.hora)
                
                if tipo == 'anticipado':
                    prog = dt - timedelta(days=config.dias_antes)
                elif tipo == 'jornada':
                    prog = dt - timedelta(minutes=config.minutos_antes_jornada)
                else:
                    print(f"DEBUG: Tipo desconocido: {tipo}")
                    continue
                
                # Buscar si ya existe la notificación para este turno y tipo
                obj, created = NotificacionEncolada.objects.get_or_create(
                    turno=turno,
                    tipo=tipo,
                    defaults={'fecha_programada': prog, 'estado': 'pendiente'}
                )
                
                if not created:
                    # Si ya existe, comprobamos si podemos volver a enviarla
                    if obj.estado == 'procesando':
                        print(f"DEBUG: Item {obj.id} ya está en proceso. Saltando.")
                        ya_estaban += 1
                        continue
                    
                    # Si ya se envió, permitimos reactivarla si el usuario lo forzó desde el radar
                    if obj.estado == 'enviado':
                        ya_estaban += 1
                    
                    obj.estado = 'pendiente'
                    obj.intentos = 0
                    obj.fecha_programada = prog
                    obj.save()
                    print(f"DEBUG: Reactivado item existente ID {obj.id}")
                else:
                    creadas += 1
                    print(f"DEBUG: Creado nuevo item ID {obj.id}")
                
                ids_a_procesar.append(obj.id)
            except Exception as e:
                print(f"DEBUG Error procesando item {val}: {e}")
                continue
        
        print(f"DEBUG: IDs finales a procesar: {ids_a_procesar}")
        if ids_a_procesar:
            try:
                enviados, errores = NotificationService.ejecutar_vigilancia(specific_ids=ids_a_procesar)
                print(f"DEBUG: Ejecución vigilancia terminada. Enviados: {enviados}, Errores: {errores}")
                
                if errores == 0:
                    if enviados == 1:
                        messages.success(request, "¡Éxito! Se envió la notificación correctamente.")
                    else:
                        messages.success(request, f"¡Logrado! Se enviaron {enviados} notificaciones exitosamente.")
                else:
                    messages.warning(request, f"Proceso finalizado: {enviados} enviados con éxito, {errores} con errores.")
            except Exception as e:
                import traceback
                traceback.print_exc()
                messages.error(request, f"Error crítico al procesar envíos: {str(e)}")
        else:
            if items:
                messages.info(request, f"Los {len(items)} elementos seleccionados ya estaban procesados o en cola.")
            else:
                messages.info(request, "No se seleccionaron turnos para procesar.")
            
    return redirect('notifications_dashboard')


def api_get_proyeccion(request):
    """
    Retorna la proyección en formato JSON según los días solicitados.
    """
    try:
        dias = int(request.GET.get('dias', 7))
    except:
        dias = 7
        
    try:
        offset = int(request.GET.get('offset', 0))
    except:
        offset = 0
        
    # Overrides opcionales desde el Wizard
    overrides = {}
    if 'dias_antes' in request.GET: overrides['dias_antes'] = request.GET.get('dias_antes')
    if 'minutos_antes_jornada' in request.GET: overrides['minutos_antes_jornada'] = request.GET.get('minutos_antes_jornada')
    if 'activar_anticipado' in request.GET: overrides['activar_anticipado'] = request.GET.get('activar_anticipado').lower() == 'true'
    if 'activar_jornada' in request.GET: overrides['activar_jornada'] = request.GET.get('activar_jornada').lower() == 'true'

    proyeccion = NotificationService.calcular_proyeccion(dias, offset, overrides=overrides if overrides else None)
    
    data = [{
        'turno_id': item['turno'].id,
        'tipo': item['tipo'],
        'tipo_display': item['tipo_display'],
        'fecha_programada': item['fecha_programada'].isoformat(),
        'responsable': item['responsable'].nombre,
        'fecha_turno': item['turno'].fecha.strftime("%d/%m/%Y"),
        'hora_turno': item['turno'].hora.strftime("%H:%M"),
        'ya_procesado': item['ya_procesado'],
        'estado_actual': item['estado_actual'],
        'missing_email': item.get('missing_email', False)
    } for item in proyeccion]
    
    from django.http import JsonResponse
    response = JsonResponse({'items': data})
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

def get_render_preview(request):
    """
    Renderiza el contenido (asunto y cuerpo) para un turno y tipo específico,
    aplicando las variables dinámicas de la configuración actual.
    """
    turno_id = request.GET.get('turno_id')
    tipo = request.GET.get('tipo', 'anticipado')
    
    if not turno_id:
        return JsonResponse({'error': 'Falta turno_id'}, status=400)
    
    from collections import namedtuple
    
    turno = get_object_or_404(Turno, id=turno_id)
    config = ConfiguracionNotificacion.get_solo()
    
    # Mock para usar la lógica compartida de services.py
    MockItem = namedtuple('MockItem', ['turno', 'get_tipo_display', 'tipo'])
    item = MockItem(
        turno=turno, 
        get_tipo_display=lambda: 'Recordatorio Anticipado' if tipo == 'anticipado' else 'Día del Turno',
        tipo=tipo
    )
    
    try:
        subject, body = NotificationService._preparar_contenido(item, config)
        return JsonResponse({
            'subject': subject,
            'body': body,
            'tipo_display': item.get_tipo_display(),
            'funcionario': turno.responsable.nombre,
            'fecha': turno.fecha.strftime("%d de %B del %Y"),
            'hora': turno.hora.strftime("%H:%M %p")
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


def reenviar_notificacion(request, pk):
    """
    Reintenta el envío de una notificación de la COLA.
    """
    if request.method == 'POST':
        # Nota: pk ahora se refiere a NotificacionEncolada.id
        success, message = NotificationService.reenviar_individual(pk)
        if success:
            messages.success(request, "Notificación reenviada con éxito.")
        else:
            messages.error(request, f"Error al reenviar: {message}")
    return redirect('notifications_dashboard')

def editar_reenviar(request, pk):
    """
    pk es el ID de la NotificacionEncolada.
    """
    if request.method == 'POST':
        item = get_object_or_404(NotificacionEncolada, pk=pk)
        nuevo_email = request.POST.get('email', '').strip()
        
        if not nuevo_email:
            messages.error(request, "El correo electrónico no puede estar vacío.")
            return redirect('notifications_dashboard')
            
        try:
            # 1. Actualizar el correo del responsable
            if item.turno:
                responsable = item.turno.responsable
                responsable.email = nuevo_email
                responsable.save()
            
            # 2. Auditoría
            AuditLogNotificaciones.objects.create(
                notificacion=item,
                accion="Edición de correo",
                detalles=f"Cambiado a {nuevo_email}"
            )
            
            messages.success(request, f"¡Cambio guardado! El correo de {item.turno.responsable.nombre} fue actualizado. Ahora puedes usar el botón de reenvío si lo deseas.")
                
        except Exception as e:
            messages.error(request, f"Error al procesar: {str(e)}")
            
    return redirect('/notifications/dashboard/?tab=history')

def cancelar_notificacion(request, pk):
    """
    Cancela una notificación de la cola.
    """
    if request.method == 'POST':
        item = get_object_or_404(NotificacionEncolada, pk=pk)
        item.estado = 'cancelado'
        item.save()  
        
        AuditLogNotificaciones.objects.create(
            notificacion=item,
            accion="Cancelación Manual",
            detalles="Usuario canceló la notificación desde el dashboard."
        )
        
        messages.info(request, "Notificación cancelada.")
    return redirect('notifications_dashboard')

def notificaciones_masivas(request):
    """
    Acciones masivas sobre la COLA.
    """
    if request.method == 'POST':
        accion = request.POST.get('accion')
        ids = request.POST.getlist('notificaciones')
        
        if not ids:
            messages.warning(request, "No seleccionaste elementos.")
            return redirect('notifications_dashboard')
            
        if accion == 'reenviar':
            exitos, errores = NotificationService.reenviar_masivo(ids)
            if errores == 0:
                messages.success(request, f"¡Acción masiva lograda! {exitos} notificaciones enviadas correctamente.")
            else:
                messages.warning(request, f"Se enviaron {exitos} con éxito, pero hubo {errores} fallos. Sugerencia: Revisa los estados 'ERROR' en la bitácora para corregir correos puntuales.")
        
        elif accion == 'cancelar':
            count = NotificacionEncolada.objects.filter(id__in=ids).update(estado='cancelado')
            messages.info(request, f"Se cancelaron {count} notificaciones.")
            
    return redirect('notifications_dashboard')


# ============================================================
# NUEVOS ENDPOINTS PARA EL SISTEMA MANUAL DE NOTIFICACIONES
# ============================================================

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.db.models import Q

@require_http_methods(["GET"])
def get_recipient_candidates(request):
    """
    API endpoint para obtener lista de funcionarios con turnos próximos.
    Para el Paso 2 del wizard de creación manual.
    """
    from datetime import timedelta
    
    # Obtener turnos de los próximos 30 días
    hoy = timezone.now().date()
    fecha_limite = hoy + timedelta(days=30)
    
    turnos_proximos = Turno.objects.filter(
        fecha__gte=hoy,
        fecha__lte=fecha_limite,
        estado='asignado'
    ).select_related('responsable').order_by('fecha', 'hora')
    
    candidatos = []
    vistos = set()  # Para evitar duplicados
    
    for turno in turnos_proximos:
        if not turno.responsable:
            continue
            
        # Evitar duplicados (mismo responsable con múltiples turnos)
        if turno.responsable.id in vistos:
            continue
        vistos.add(turno.responsable.id)
        
        config_crono = ConfiguracionCronograma.objects.first()
        duracion = config_crono.duracion_turno if config_crono else 30
        
        candidatos.append({
            'id': turno.responsable.id,
            'turno_id': turno.id,
            'nombre': turno.responsable.nombre,
            'email': turno.responsable.email or 'Sin correo',
            'fecha_turno': turno.fecha.strftime("%d/%m/%Y"),
            'hora': turno.hora.strftime("%H:%M") if turno.hora else "N/A",
            'duracion': duracion,
            'missing_email': not bool(turno.responsable.email)
        })
    
    return JsonResponse({'candidatos': candidatos})


@require_http_methods(["POST"])
def send_manual_notifications(request):
    """
    Envía notificaciones manuales de forma inmediata.
    Recibe: asunto, mensaje, recipient_ids[]
    """
    import json
    
    try:
        data = json.loads(request.body)
        asunto = data.get('asunto', '')
        mensaje = data.get('mensaje', '')
        recipient_ids = data.get('recipient_ids', [])
        
        if not asunto or not mensaje:
            return JsonResponse({'error': 'Asunto y mensaje son requeridos'}, status=400)
        
        if not recipient_ids:
            return JsonResponse({'error': 'Debes seleccionar al menos un destinatario'}, status=400)
        
        # Crear notificaciones para cada destinatario
        notificaciones_creadas = []
        
        for resp_id in recipient_ids:
            try:
                responsable = Responsable.objects.get(id=resp_id)
                
                # Buscar el turno más próximo de este responsable
                turno = Turno.objects.filter(
                    responsable=responsable,
                    fecha__gte=timezone.now().date(),
                    estado='asignado'
                ).order_by('fecha', 'hora').first()
                
                if not turno:
                    continue
                
                # Crear notificación en cola para envío inmediato
                notif = NotificacionEncolada.objects.create(
                    turno=turno,
                    tipo='manual',
                    fecha_programada=timezone.now(),
                    estado='pendiente'
                )
                notificaciones_creadas.append(notif.id)
                
            except Responsable.DoesNotExist:
                continue
        
        # Ejecutar envío inmediato
        enviados, errores = NotificationService.ejecutar_vigilancia(specific_ids=notificaciones_creadas)
        
        return JsonResponse({
            'success': True,
            'enviados': enviados,
            'errores': errores,
            'total': len(notificaciones_creadas)
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Formato JSON inválido'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@require_http_methods(["POST"])
def retry_notification(request, notification_id):
    """
    Reintenta el envío de una notificación que falló.
    Puede recibir un nuevo email opcional.
    """
    import json
    
    try:
        notif = get_object_or_404(NotificacionEncolada, id=notification_id)
        
        data = json.loads(request.body)
        nuevo_email = data.get('email', None)
        
        # Si se proporciona un nuevo email, actualizarlo
        if nuevo_email and notif.turno and notif.turno.responsable:
            notif.turno.responsable.email = nuevo_email
            notif.turno.responsable.save()
        
        # Reintentar envío
        enviados, errores = NotificationService.ejecutar_vigilancia(specific_ids=[notification_id])
        
        # Recargar para obtener el estado actualizado
        notif.refresh_from_db()
        
        return JsonResponse({
            'success': enviados > 0,
            'estado': notif.estado,
            'ultimo_error': notif.ultimo_error
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Formato JSON inválido'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@require_http_methods(["POST"])
def edit_notification(request, notification_id):
    """
    Edita una notificación fallida y opcionalmente la reenvía.
    """
    import json
    
    try:
        notif = get_object_or_404(NotificacionEncolada, id=notification_id)
        
        data = json.loads(request.body)
        email = data.get('email', None)
        asunto = data.get('asunto', None)
        mensaje = data.get('mensaje', None)
        auto_retry = data.get('auto_retry', False)
        
        # Actualizar email si se proporciona
        if email and notif.turno and notif.turno.responsable:
            notif.turno.responsable.email = email
            notif.turno.responsable.save()
        
        # Para asunto y mensaje, tendríamos que almacenarlos en un campo adicional
        # o usar la configuración global. Por ahora, solo registramos el cambio
        
        if auto_retry:
            enviados, errores = NotificationService.ejecutar_vigilancia(specific_ids=[notification_id])
            notif.refresh_from_db()
            
            return JsonResponse({
                'success': enviados > 0,
                'estado': notif.estado,
                'reenviado': True
            })
        else:
            return JsonResponse({
                'success': True,
                'mensaje': 'Cambios guardados'
            })
        
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Formato JSON inválido'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@require_http_methods(["GET"])
def get_notification_details(request, notification_id):
    """
    Obtiene todos los detalles de una notificación incluyendo historial de intentos.
    """
    try:
        notif = get_object_or_404(NotificacionEncolada, id=notification_id)
        
        # Obtener historial de intentos
        historial = HistorialEnvio.objects.filter(
            notificacion=notif
        ).order_by('-fecha_envio').values(
            'fecha_envio', 'estado', 'error_log', 'intento_n', 'asunto', 'cuerpo'
        )
        
        return JsonResponse({
            'id': notif.id,
            'funcionario': notif.turno.responsable.nombre if notif.turno and notif.turno.responsable else 'N/A',
            'email': notif.turno.responsable.email if notif.turno and notif.turno.responsable else 'N/A',
            'estado': notif.estado,
            'tipo': notif.get_tipo_display(),
            'fecha_programada': notif.fecha_programada.isoformat(),
            'intentos': notif.intentos,
            'ultimo_error': notif.ultimo_error,
            'historial': list(historial)
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@require_http_methods(["GET"])
def filter_historial(request):
    """
    Filtra el historial de notificaciones según criterios.
    """
    fecha_desde = request.GET.get('fecha_desde', None)
    fecha_hasta = request.GET.get('fecha_hasta', None)
    estado = request.GET.get('estado', None)
    search_name = request.GET.get('search_name', None)
    
    queryset = HistorialEnvio.objects.select_related('turno', 'turno__responsable').all()
    
    if fecha_desde:
        try:
            fecha_desde_dt = datetime.strptime(fecha_desde, '%Y-%m-%d').date()
            queryset = queryset.filter(fecha_envio__date__gte=fecha_desde_dt)
        except ValueError:
            pass
    
    if fecha_hasta:
        try:
            fecha_hasta_dt = datetime.strptime(fecha_hasta, '%Y-%m-%d').date()
            queryset = queryset.filter(fecha_envio__date__lte=fecha_hasta_dt)
        except ValueError:
            pass
    
    if estado:
        queryset = queryset.filter(estado=estado)
    
    if search_name:
        queryset = queryset.filter(
            Q(turno__responsable__nombre__icontains=search_name) |
            Q(destinatario__icontains=search_name)
        )
    
    # Limitar a 100 resultados y ordenar
    resultados = queryset.order_by('-fecha_envio')[:100]
    
    data = []
    for item in resultados:
        data.append({
            'id': item.id,
            'fecha_envio': item.fecha_envio.isoformat(),
            'funcionario': item.turno.responsable.nombre if item.turno and item.turno.responsable else 'N/A',
            'email': item.destinatario,
            'fecha_turno': item.turno.fecha.strftime("%d/%m/%Y") if item.turno and item.turno.fecha else 'N/A',
            'hora_turno': item.turno.hora.strftime("%H:%M") if item.turno and item.turno.hora else 'N/A',
            'estado': item.estado,
            'asunto': item.asunto,
        })
    
    return JsonResponse({'resultados': data})


def guardar_configuracion(request):
    """
    Vista para guardar la configuración global de notificaciones.
    """
    if request.method == 'POST':
        config = ConfiguracionNotificacion.get_solo()
        
        asunto = request.POST.get('asunto_template')
        cuerpo = request.POST.get('cuerpo_template')
        activar_ant = request.POST.get('activar_anticipado') == 'on'
        dias_antes = request.POST.get('dias_antes')
        
        if asunto: config.asunto_template = asunto
        if cuerpo: config.cuerpo_template = cuerpo
        
        config.activar_anticipado = activar_ant
        try:
            if dias_antes: config.dias_antes = int(dias_antes)
        except: pass
        
        config.save()
        
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1':
            return JsonResponse({'status': 'ok', 'message': 'Configuración guardada correctamente'})
            
        messages.success(request, "Configuración global actualizada correctamente.")
        
    return redirect('notifications_dashboard')
