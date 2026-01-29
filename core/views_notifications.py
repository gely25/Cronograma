from django.shortcuts import render, redirect
from django.db.models import Q
from django.core.mail import send_mail
from django.conf import settings
from django.contrib import messages
from django.utils import timezone
from .models import Turno, ConfiguracionCronograma

def notification_manager(request):
    """
    Vista para gestionar el envío manual de notificaciones.
    """
    
    # Filtros iniciales
    estado_planificado = request.GET.get('estado_planificado') == 'on'
    estado_finalizado = request.GET.get('estado_finalizado') == 'on'
    estado_en_curso = request.GET.get('estado_en_curso') == 'on'
    estado_cerrado = request.GET.get('estado_cerrado') == 'on'
    
    fecha_inicio = request.GET.get('fecha_inicio')
    fecha_fin = request.GET.get('fecha_fin')

    turnos_filtrados = Turno.objects.all().select_related('responsable').prefetch_related('responsable__equipos')

    # Filtrado por estado (mapeo aproximado a los del modelo)
    # Planificado -> Pendiente/Asignado
    # Finalizado -> Completado
    # En curso -> En Proceso
    estados_query = Q()
    if estado_planificado:
        estados_query |= Q(estado__in=['pendiente', 'asignado'])
    if estado_en_curso:
        estados_query |= Q(estado='en_proceso')
    if estado_finalizado or estado_cerrado:
        estados_query |= Q(estado='completado')
    
    if estados_query:
        turnos_filtrados = turnos_filtrados.filter(estados_query)
    
    # Filtrado por fecha
    if fecha_inicio:
        turnos_filtrados = turnos_filtrados.filter(fecha__gte=fecha_inicio)
    if fecha_fin:
        turnos_filtrados = turnos_filtrados.filter(fecha__lte=fecha_fin)

    # Preview data: Get the first one to show in the UI card
    preview_turno = turnos_filtrados.first()
    count = turnos_filtrados.count()

    if request.method == 'POST':
        mensaje_base = request.POST.get('mensaje', '')
        # Verificar acción
        accion = request.POST.get('accion') # enviar o cancelar
        
        if accion == 'enviar':
            enviados = 0
            for turno in turnos_filtrados:
                email_destino = turno.responsable.email
                if not email_destino:
                    continue
                
                # Reemplazo de variables simples si se quisiera (opcional)
                mensaje = mensaje_base # Por ahora texto plano/html directo
                
                try:
                    send_mail(
                        "Notificación Mantenimiento de Equipos Tecnológicos",
                        mensaje, # Version texto plano (si el input es HTML, esto debería ser stripped)
                        settings.DEFAULT_FROM_EMAIL,
                        [email_destino],
                        html_message=mensaje, # Version HTML
                        fail_silently=False
                    )
                    turno.notificacion_enviada = True
                    turno.ultimo_envio = timezone.now()
                    turno.save()
                    enviados += 1
                except Exception as e:
                    print(f"Error enviando a {email_destino}: {e}")
            
            messages.success(request, f"Se enviaron {enviados} notificaciones correctamente.")
            return redirect('notification_manager')

    context = {
        'preview_turno': preview_turno,
        'count': count,
        'fecha_inicio': fecha_inicio,
        'fecha_fin': fecha_fin,
    }
    return render(request, 'core/notification_manager.html', context)
