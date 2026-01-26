from django.shortcuts import render, redirect
from django.contrib import messages
from .forms import UploadFileForm
from .services import procesar_archivo_activos, generar_cronograma
from .models import Turno

def index(request):
    return redirect('upload_excel')

def upload_excel(request):
    if request.method == 'POST':
        form = UploadFileForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                procesar_archivo_activos(request.FILES['archivo'])
                generar_cronograma() # Genera por defecto para Febrero 2026
                messages.success(request, "Archivo procesado y cronograma generado con éxito.")
                return redirect('ver_cronograma')
            except Exception as e:
                messages.error(request, f"Error al procesar el archivo: {e}")
    else:
        form = UploadFileForm()
    
    return render(request, 'core/upload.html', {'form': form})

from datetime import time, timedelta, datetime
from .models import Turno, Dispositivo

def ver_cronograma(request):
    turnos = Turno.objects.select_related('responsable').prefetch_related('responsable__dispositivos').all().order_by('fecha', 'hora_inicio')
    
    # Obtener dispositivos para el sidebar (aquellos cuyo responsable aún no tiene turno asignado)
    dispositivos_agendados = Turno.objects.values_list('responsable_id', flat=True)
    dispositivos = Dispositivo.objects.exclude(responsable_id__in=dispositivos_agendados).order_by('-id')[:20]
    
    # Preparar franjas horarias (08:00 a 17:00 cada 30 min)
    horarios = []
    curr = datetime.combine(datetime.today(), time(8, 0))
    end = datetime.combine(datetime.today(), time(17, 0))
    while curr <= end:
        horarios.append(curr.time())
        curr += timedelta(minutes=30)
    
    # Agrupar turnos por hora para la vista de grilla (3 estaciones simuladas)
    primer_dia = turnos.first().fecha if turnos.exists() else None
    filas_grilla = [] # [{'hora': h, 'slots': [t1, t2, t3]}]
    
    if primer_dia:
        dia_turnos = turnos.filter(fecha=primer_dia)
        for h in horarios:
            slots = [None, None, None]
            # Buscar turnos de esta hora para cada estación
            for i in range(1, 4):
                slots[i-1] = dia_turnos.filter(hora_inicio=h, estacion=i).first()
            
            filas_grilla.append({
                'hora': h,
                'slots': slots
            })

    context = {
        'turnos': turnos,
        'dispositivos': dispositivos,
        'filas_grilla': filas_grilla,
        'fecha_actual': primer_dia,
    }
    return render(request, 'core/cronograma.html', context)
