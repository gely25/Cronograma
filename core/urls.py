from django.urls import path
from . import views
from . import views_notifications

urlpatterns = [
    path('notificaciones/', views_notifications.notification_manager, name='notification_manager'),
    path('', views.index, name='index'),
    path('upload/', views.upload_excel, name='upload_excel'),
    path('cronograma/', views.ver_cronograma, name='ver_cronograma'),
    path('config/guardar/', views.guardar_configuracion, name='guardar_configuracion'),
    path('cronograma/generar/', views.generar_cronograma_view, name='generar_cronograma'),
    path('turno/<int:turno_id>/actualizar/', views.actualizar_turno, name='actualizar_turno'),
    path('turno/intercambiar/', views.intercambiar_turnos, name='intercambiar_turnos'),
    path('turno/<int:turno_id>/toggle/', views.toggle_completado, name='toggle_completado'),
    path('equipo/<int:equipo_id>/toggle-atendido/', views.toggle_equipo_atendido, name='toggle_equipo_atendido'),
    path('feriados/add/', views.add_feriado, name='add_feriado'),
    path('feriados/remove/', views.remove_feriado, name='remove_feriado'),
    path('exportar/', views.exportar_excel, name='exportar_excel'),
    path('api/datos/', views.api_get_datos, name='api_get_datos'),
    path('api/day/<str:date>/shifts/', views.get_day_shifts, name='get_day_shifts'),
    path('reset-database/', views.reset_database, name='reset_database'),
    path('api/turnos/crear-manual/', views.crear_turno_manual, name='crear_turno_manual'),
    
    # PWA Support at root
    path('sw.js', views.service_worker, name='sw.js'),
    path('manifest.json', views.manifest_view, name='manifest.json'),
]
