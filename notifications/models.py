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
        default="Recordatorio: {evento} - {fecha}",
        verbose_name="Plantilla de Asunto"
    )
    cuerpo_template = models.TextField(
        default="Hola {funcionario},\n\nTienes una actividad programada:\n\nEvento: {evento}\nFecha: {fecha}\nHora: {hora}\n\nPor favor, asegúrate de estar preparado.",
        verbose_name="Plantilla de Cuerpo"
    )
    
    # Notificación de Inicio (Broadcast)
    asunto_inicio = models.CharField(
        max_length=255,
        default="Inicio de Cronograma: Mantenimiento Preventivo",
        verbose_name="Asunto Notificación Inicio"
    )
    cuerpo_inicio = models.TextField(
        default="Estimados funcionarios,\n\nSe ha generado el nuevo cronograma de mantenimiento. A partir de ahora recibirán notificaciones automáticas 1 hora antes de sus turnos asignados.\n\nSaludos,\nGestión de Activos.",
        verbose_name="Cuerpo Notificación Inicio"
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

class HistorialEnvio(models.Model):
    """
    Bitácora de Vuelo. Solo registra lo que YA ocurrió.
    Reemplaza a NotificacionProgramada (que mezclaba pendientes con enviados).
    """
    TIPO_CHOICES = [
        ('anticipado', 'Recordatorio Anticipado'),
        ('jornada', 'Inicio de Jornada'),
    ]
    
    ESTADO_CHOICES = [
        ('enviado', 'Enviado Exitosamente'),
        ('fallido', 'Falló el Envío'),
    ]

    turno = models.ForeignKey(Turno, on_delete=models.CASCADE, related_name='historial_notificaciones')
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    
    fecha_envio = models.DateTimeField(auto_now_add=True, db_index=True)
    
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='enviado')
    
    # Datos del correo enviado (Snapshot)
    destinatario = models.EmailField()
    asunto = models.CharField(max_length=255)
    cuerpo = models.TextField(blank=True) # Guardar el cuerpo enviado
    error_log = models.TextField(blank=True)

    class Meta:
        ordering = ['-fecha_envio']
        indexes = [
            models.Index(fields=['fecha_envio', 'estado']),
        ]

    def __str__(self):
        return f"{self.fecha_envio} - {self.destinatario} ({self.estado})"
