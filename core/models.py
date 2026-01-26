from django.db import models

class Responsable(models.Model):
    nombre = models.CharField(max_length=255, unique=True, verbose_name="Responsable")

    def __str__(self):
        return self.nombre

class Dispositivo(models.Model):
    responsable = models.ForeignKey(Responsable, on_delete=models.CASCADE, related_name='dispositivos')
    codigo_interno = models.CharField(max_length=100, blank=True, null=True)
    cpdogp_gobierno = models.CharField(max_length=100, blank=True, null=True)
    fecha_ingreso = models.CharField(max_length=100, blank=True, null=True) # Using CharField as excel dates can be tricky or messy
    descripcion = models.TextField(blank=True, null=True)
    modelo = models.CharField(max_length=100, blank=True, null=True)
    marca = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return f"{self.marca} {self.modelo} ({self.codigo_interno})"

class Turno(models.Model):
    responsable = models.OneToOneField(Responsable, on_delete=models.CASCADE, related_name='turno')
    fecha = models.DateField()
    hora_inicio = models.TimeField()
    hora_fin = models.TimeField()
    estacion = models.IntegerField(default=1) # 1, 2, or 3 representing the columns in the ERP

    class Meta:
        ordering = ['fecha', 'hora_inicio', 'estacion']

    def __str__(self):
        return f"Turno {self.responsable}: {self.fecha} {self.hora_inicio}-{self.hora_fin}"
