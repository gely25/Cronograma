from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponse
from django.db.models import Count
from django.utils import timezone
from datetime import datetime, timedelta
import json
from .models import ConfiguracionNotificacion, HistorialEnvio, NotificacionEncolada, AuditLogNotificaciones
from .services import NotificationService
from core.models import Turno

def dashboard(request):
    """
    Vista principal del Sistema Inteligente de Notificaciones (Observer Pattern).
    Muestra:
    1. Configuración (Reglas) con sincronización.
    2. Radar (Cola de Notificaciones Pendientes en BD)
    3. Historial (Bitácora de intentos reales)
    """
    config = ConfiguracionNotificacion.get_solo()
    
    if request.method == 'POST':
        # Actualizar Configuración
        config.activar_anticipado = request.POST.get('activar_anticipado') == 'on'
        config.dias_antes = int(request.POST.get('dias_antes', 1))
        config.activar_jornada = request.POST.get('activar_jornada') == 'on'
        config.minutos_antes_jornada = int(request.POST.get('minutos_antes_jornada', 60))
        
        config.asunto_template = request.POST.get('asunto_template', '')
        config.cuerpo_template = request.POST.get('cuerpo_template', '')
        
        config.save()
        messages.success(request, "Configuración del Gestor de Notificaciones actualizada.")
        return redirect('notifications_dashboard')
        
    # --- COLA PENDIENTE (RADAR REAL) ---
    # Mostramos lo que está en la base de datos pendiente de enviarse
    cola_radar = NotificacionEncolada.objects.filter(
        estado__in=['pendiente', 'error_temporal']
    ).select_related('turno', 'turno__responsable').order_by('fecha_programada')
    
    #Stats reales de la arquitectura robusta
    stats_data = {
        'total_esperadas': NotificacionEncolada.objects.exclude(estado='cancelado').count(),
        'enviadas': NotificacionEncolada.objects.filter(estado='enviado').count(),
        'fallidas': NotificacionEncolada.objects.filter(estado='fallido').count(),
        'pendientes': NotificacionEncolada.objects.filter(estado__in=['pendiente', 'error_temporal']).count()
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
        equipos = resp.equipos.all()
        if equipos.exists():
            lista = "\n".join([f"• {e.marca} {e.modelo} (Cód: {e.codigo or 'N/A'})" for e in equipos])
            marca = equipos.first().marca
            modelo = equipos.first().modelo
        else:
            lista = "Equipo informático general"
            marca = "Hardware"
            modelo = "General"
            
        # Obtener duración real
        from core.models import ConfiguracionCronograma
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
    # --- HISTORIAL (AUDITORÍA) ---
    historial = HistorialEnvio.objects.select_related(
        'turno', 
        'turno__responsable'
    ).order_by('-fecha_envio')[:50]

    # --- PROYECCIÓN (RADAR SEMANAL) ---
    proyeccion = NotificationService.calcular_proyeccion(7)
    
    context = {
        'config': config,
        'stats': stats_data,
        'cola_radar': cola_radar,
        'proyeccion': proyeccion,
        'ejemplo_real': ejemplo_data,
        'proyeccion_json': json.dumps([{
            'responsable': {'nombre': item['responsable'].nombre if item.get('responsable') else 'Broadcast'},
            'tipo': item['tipo'],
            'fecha_programada': item['fecha_programada'].isoformat(),
            'fecha_turno': item['turno'].fecha.strftime("%d/%m/%Y") if item.get('turno') and item['turno'].fecha else '',
            'hora_turno': item['turno'].hora.strftime("%H:%M") if item.get('turno') and item['turno'].hora else ''
        } for item in proyeccion[:20]]), # Ahora sí es el radar de proyección real
        'historial': historial,
        'estados_choices': Turno.ESTADO_CHOICES
    }
    return render(request, 'notifications/dashboard.html', context)

def sincronizar_cola_view(request):
    """
    Controlador para forzar la sincronización de la cola con los turnos.
    """
    if request.method == 'POST':
        creadas = NotificationService.sincronizar_cola()
        if creadas > 0:
            messages.success(request, f"Se han programado {creadas} nuevas notificaciones en la cola.")
        else:
            messages.info(request, "La cola ya está sincronizada con los turnos actuales.")
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


def generar_desde_proyeccion(request):
    """
    Toma selecciones del Radar de Proyección y las convierte en registros de la COLA.
    """
    if request.method == 'POST':
        items = request.POST.getlist('proyeccion_items')
        config = ConfiguracionNotificacion.get_solo()
        creadas = 0
        
        for val in items:
            try:
                t_id, tipo = val.split(':')
                from core.models import Turno 
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
                    continue
                
                _, created = NotificacionEncolada.objects.get_or_create(
                    turno=turno,
                    tipo=tipo,
                    defaults={'fecha_programada': prog}
                )
                if created: creadas += 1
            except:
                continue
        
        if creadas > 0:
            messages.success(request, f"Se han programado {creadas} notificaciones exitosamente.")
        else:
            messages.info(request, "Las notificaciones seleccionadas ya se encontraban en la cola.")
            
    return redirect('notifications_dashboard')


def api_get_proyeccion(request):
    """
    Retorna la proyección en formato JSON según los días solicitados.
    """
    try:
        dias = int(request.GET.get('dias', 7))
    except:
        dias = 7
        
    proyeccion = NotificationService.calcular_proyeccion(dias)
    
    data = [{
        'turno_id': item['turno'].id,
        'tipo': item['tipo'],
        'tipo_display': item['tipo_display'],
        'fecha_programada': item['fecha_programada'].isoformat(),
        'responsable': item['responsable'].nombre,
        'fecha_turno': item['turno'].fecha.strftime("%d/%m/%Y"),
        'hora_turno': item['turno'].hora.strftime("%H:%M")
    } for item in proyeccion]
    
    from django.http import JsonResponse
    response = JsonResponse({'items': data})
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


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
            
            # 3. Reenviar
            success, message = NotificationService.reenviar_individual(pk)
            if success:
                messages.success(request, f"¡Envío logrado! El correo de {item.turno.responsable.nombre} fue actualizado y enviado correctamente.")
            else:
                messages.error(request, f"No se pudo completar el envío: {message}. Info: Verifica si el correo es válido o si hay conexión con el servidor de salida.")
                
        except Exception as e:
            messages.error(request, f"Error al procesar: {str(e)}")
            
    return redirect('notifications_dashboard')

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
