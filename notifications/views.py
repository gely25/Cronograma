from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponse
from django.db.models import Count
from django.utils import timezone
import json
from .models import ConfiguracionNotificacion, HistorialEnvio
from .services import NotificationService
from core.models import Turno

def dashboard(request):
    """
    Vista principal del Sistema Inteligente de Notificaciones (Observer Pattern).
    Muestra:
    1. Configuración (Reglas)
    2. Radar (Proyección calculada al vuelo)
    3. Historial (Bitácora real)
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
        
        config.asunto_inicio = request.POST.get('asunto_inicio', '')
        config.cuerpo_inicio = request.POST.get('cuerpo_inicio', '')
        config.actividad_general = request.POST.get('actividad_general', 'Mantenimiento Preventivo')
        
        config.cc_email = request.POST.get('cc_email', '')
        
        config.save()
        messages.success(request, "Configuración del Gestor de Notificaciones actualizada.")
        return redirect('notifications_dashboard')
        
    # --- PROYECCIÓN (RADAR) ---
    # Calculamos qué se enviaría en los próximos 7 días según las reglas actuales
    proyeccion = NotificationService.calcular_proyeccion(dias=7)
    
    #Stats rápidos
    total_turnos = Turno.objects.count()
    total_proyeccion = len(proyeccion)
    
    # --- HISTORIAL (BITÁCORA) ---
    historial = HistorialEnvio.objects.select_related(
        'turno', 
        'turno__responsable'
    ).prefetch_related(
        'turno__responsable__equipos'
    ).order_by('-fecha_envio')[:50] # Últimos 50
    
    enviados_count = HistorialEnvio.objects.filter(estado='enviado').count()
    fallidos_count = HistorialEnvio.objects.filter(estado='fallido').count()
    
    context = {
        'config': config,
        'stats': {
            'total_turnos': total_turnos,
            'enviadas': enviados_count,
            'fallidas': fallidos_count,
            'proyeccion': total_proyeccion
        },
        'proyeccion': proyeccion, # Para vista de Radar
        'proyeccion_json': json.dumps([{
            'responsable': {'nombre': p['responsable'].nombre if p.get('responsable') else ''},
            'tipo': p.get('tipo', ''),
            'fecha_programada': p['fecha_programada'].isoformat() if p.get('fecha_programada') else '',
            'fecha_turno': p['turno'].fecha.strftime("%d/%m/%Y") if p.get('turno') and p['turno'].fecha else '',
            'hora_turno': p['turno'].hora.strftime("%H:%M") if p.get('turno') and p['turno'].hora else ''
        } for p in proyeccion]),
        'historial': historial,   # Para vista de Log
        'estados_choices': Turno.ESTADO_CHOICES
    }
    return render(request, 'notifications/dashboard.html', context)

def ejecutar_envios(request):
    """
    Endpoint para despertar al Observador manualmente.
    """
    if request.method == 'POST':
        enviados, errores = NotificationService.ejecutar_vigilancia()
        total = enviados + errores
        if total == 0:
             messages.info(request, "El Observador no encontró notificaciones para enviar en este momento.")
        elif errores > 0:
            messages.warning(request, f"Vigilancia completada: {enviados} enviados, {errores} fallidos.")
        else:
            messages.success(request, f"Vigilancia completada: {enviados} correos enviados.")
    
    return redirect('notifications_dashboard')

def notificar_inicio(request):
    """
    Despacha la notificación masiva de inicio a todos los funcionarios.
    """
    if request.method == 'POST':
        try:
            cantidad = NotificationService.enviar_broadcast_inicio()
            if cantidad > 0:
                messages.success(request, f"¡Éxito! Se ha notificado el inicio a {cantidad} funcionarios.")
            else:
                messages.warning(request, "No se encontraron funcionarios con turnos activos para notificar.")
        except Exception as e:
            messages.error(request, f"Error crítico al enviar notificación masiva: {str(e)}")
            print(f"Broadcast Error: {e}")
    
    return redirect('notifications_dashboard')
