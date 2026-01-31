from django.db import models
from django.utils import timezone
import uuid
from core.models import Turno

class ConfiguracionNotificacion(models.Model):
    """
    Singleton para la configuración de 'Leyes de la Torre de Vigilancia'.
    """
    # Condición 1: Anticipación
    activar_anticipado = models.BooleanField(default=True, verbose_name="Activar Recordatorio Anticipado")
    dias_antes = models.PositiveIntegerField(default=1, verbose_name="Días de anticipación")
    
    # Condición 2: Recordatorio el día del turno
    activar_jornada = models.BooleanField(default=True, verbose_name="Activar Recordatorio el Día del Turno")
    minutos_antes_jornada = models.PositiveIntegerField(default=60, verbose_name="Minutos antes del turno")
    
    asunto_template = models.CharField(
        max_length=255, 
        default="Recordatorio: Mantenimiento Preventivo de Hardware ({marca})",
        verbose_name="Plantilla de Asunto"
    )
    cuerpo_template = models.TextField(
        default="Se informa que se realizará el mantenimiento preventivo de los siguientes equipos asignados a su cargo:\n\n{equipos_lista}\n\nDicha actividad se llevará a cabo el día {fecha_turno}, en las oficinas del área de Gestión de Mantenimiento, ubicadas en el Bloque C, planta alta. El horario de atención para su turno específico inicia a las {hora}, con una duración estimada de {duracion} minutos por equipo. Las actividades programadas a ejecutar son las siguientes:\n\n"
                "• Limpieza externa e interna: Eliminación de polvo y residuos de componentes internos y carcasa exterior del equipo.\n"
                "• Cambio de pasta térmica: Reemplazo de pasta térmica en procesador y componentes críticos para optimizar la transferencia de calor.\n"
                "• Diagnóstico de rendimiento: Evaluación integral del funcionamiento del sistema y verificación de parámetros operativos.\n"
                "• Actualización de software: Instalación de las últimas versiones de software y parches de seguridad disponibles.\n\n"
                "Se solicita coordinar previamente la entrega de sus equipos para garantizar el cumplimiento del cronograma establecido y evitar interrupciones en el servicio durante el período mencionado.",
        verbose_name="Plantilla de Cuerpo"
    )
    

    actividad_general = models.CharField(
        max_length=100, 
        default="Mantenimiento Preventivo",
        verbose_name="Actividad por Defecto"
    )
    cc_email = models.EmailField(blank=True, null=True, verbose_name="Copia Oculta (BCC)")
    
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1 # Force Singleton
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "Reglas de Notificación"

class NotificacionEncolada(models.Model):
    """
    COLA DE NOTIFICACIONES (Plan Maestro).
    Registra cada mensaje que DEBE ser enviado.
    """
    ESTADO_CHOICES = [
        ('pendiente', 'Pendiente'),
        ('procesando', 'En Procesamiento'),
        ('enviado', 'Enviado con Éxito'),
        ('error_temporal', 'Error Temporal (Reintentará)'),
        ('fallido', 'Fallido (Sin más reintentos)'),
        ('cancelado', 'Cancelado Manualmente'),
    ]

    TIPO_CHOICES = [
        ('anticipado', 'Recordatorio Anticipado'),
        ('jornada', 'Recordatorio del Día'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    turno = models.ForeignKey(Turno, on_delete=models.CASCADE, related_name='cola_notificaciones', null=True, blank=True)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    
    fecha_programada = models.DateTimeField(db_index=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='pendiente', db_index=True)
    
    intentos = models.PositiveIntegerField(default=0)
    max_intentos = models.PositiveIntegerField(default=3)
    
    ultimo_error = models.TextField(blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    ultima_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['fecha_programada']
        verbose_name = "Notificación Encolada"
        verbose_name_plural = "Notificaciones Encoladas"

    def __str__(self):
        dest = self.turno.responsable.nombre if self.turno else "Broadcast"
        return f"{self.tipo} para {dest} ({self.estado})"

class HistorialEnvio(models.Model):
    """
    BITÁCORA DE AUDITORÍA.
    Registra cada INTENTO real de envío (exitoso o no).
    """
    notificacion = models.ForeignKey(NotificacionEncolada, on_delete=models.CASCADE, related_name='intentos_envio', null=True, blank=True)
    turno = models.ForeignKey(Turno, on_delete=models.CASCADE, related_name='historial_notificaciones')
    
    tipo = models.CharField(max_length=20) # Snapshot del tipo
    intento_n = models.PositiveIntegerField(default=1)
    
    fecha_envio = models.DateTimeField(auto_now_add=True, db_index=True)
    estado = models.CharField(max_length=20) # Snapshot: enviado, fallido, cancelado
    
    destinatario = models.EmailField()
    asunto = models.CharField(max_length=255)
    cuerpo = models.TextField(blank=True) 
    error_log = models.TextField(blank=True)

    class Meta:
        ordering = ['-fecha_envio']

    def __str__(self):
        return f"Intento {self.intento_n} - {self.destinatario} ({self.estado})"

class AuditLogNotificaciones(models.Model):
    """
    LOG DE AUDITORÍA HUMANA.
    Registra cambios manuales realizados por el usuario.
    """
    notificacion = models.ForeignKey(NotificacionEncolada, on_delete=models.CASCADE, related_name='auditoria')
    accion = models.CharField(max_length=100) # ej: "Cancelación manual", "Edición de correo"
    usuario = models.CharField(max_length=255, blank=True) # Si hubiera sistema de perfiles
    detalles = models.TextField(blank=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha']
