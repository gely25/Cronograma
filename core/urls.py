from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('upload/', views.upload_excel, name='upload_excel'),
    path('cronograma/', views.ver_cronograma, name='ver_cronograma'),
    path('config/guardar/', views.guardar_configuracion, name='guardar_configuracion'),
    path('cronograma/generar/', views.generar_cronograma_view, name='generar_cronograma'),
    path('turno/<int:turno_id>/actualizar/', views.actualizar_turno, name='actualizar_turno'),
    path('turno/<int:turno_id>/toggle/', views.toggle_completado, name='toggle_completado'),
    path('equipo/<int:equipo_id>/toggle-atendido/', views.toggle_equipo_atendido, name='toggle_equipo_atendido'),
    path('feriados/add/', views.add_feriado, name='add_feriado'),
    path('feriados/remove/', views.remove_feriado, name='remove_feriado'),
    path('exportar/', views.exportar_excel, name='exportar_excel'),
    path('api/datos/', views.api_get_datos, name='api_get_datos'),
    path('reset-database/', views.reset_database, name='reset_database'),
]
