from django.db import models
from datetime import datetime, timedelta

class Responsable(models.Model):
    nombre = models.CharField(max_length=255, unique=True, verbose_name="Responsable")
    email = models.EmailField(max_length=255, blank=True, null=True, verbose_name="Correo Electrónico")

    def __str__(self):
        return self.nombre

class Equipo(models.Model):
    responsable = models.ForeignKey(Responsable, on_delete=models.CASCADE, related_name='equipos')
    codigo = models.CharField(max_length=100, blank=True, null=True)
    marca = models.CharField(max_length=100, blank=True, null=True)
    modelo = models.CharField(max_length=100, blank=True, null=True)
    descripcion = models.TextField(blank=True, null=True)
    atendido = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.marca} {self.modelo} ({self.codigo})"

class ConfiguracionCronograma(models.Model):
    fecha_inicio = models.DateField(null=True, blank=True)
    fecha_fin = models.DateField(null=True, blank=True)
    hora_inicio = models.TimeField(default="08:00")
    hora_fin = models.TimeField(default="17:00")
    duracion_turno = models.IntegerField(default=30)
    hora_almuerzo = models.TimeField(default="12:00")
    duracion_almuerzo = models.IntegerField(default=60)
    
    MODO_EXCLUSION_CHOICES = [
        ('none', 'No excluir'),
        ('sundays', 'Solo Domingos'),
        ('weekends', 'Sábados y Domingos'),
    ]
    modo_exclusion = models.CharField(max_length=20, choices=MODO_EXCLUSION_CHOICES, default='weekends')

    class Meta:
        verbose_name = "Configuración de Cronograma"
        verbose_name_plural = "Configuraciones de Cronograma"

class Feriado(models.Model):
    fecha = models.DateField(unique=True)
    descripcion = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return f"{self.fecha} - {self.descripcion or 'Feriado'}"

class Turno(models.Model):
    ESTADO_CHOICES = [
        ('pendiente', 'Pendiente'),
        ('asignado', 'Asignado'),
        ('en_proceso', 'En Proceso'),
        ('completado', 'Completado'),
    ]

    responsable = models.OneToOneField(Responsable, on_delete=models.CASCADE, related_name='turno')
    fecha = models.DateField(null=True, blank=True)
    hora = models.TimeField(null=True, blank=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='pendiente')
    notificar_el = models.DateTimeField(null=True, blank=True)
    notificacion_enviada = models.BooleanField(default=False)
    ultimo_envio = models.DateTimeField(null=True, blank=True, verbose_name="Último Envío")

    def save(self, *args, **kwargs):
        if self.fecha and self.hora and not self.notificar_el:
            fecha_hora_turno = datetime.combine(self.fecha, self.hora)
            self.notificar_el = fecha_hora_turno - timedelta(days=1)
        super().save(*args, **kwargs)

    class Meta:
        ordering = ['fecha', 'hora']

    def __str__(self):
        return f"Turno {self.responsable}: {self.fecha} {self.hora} ({self.estado})"
