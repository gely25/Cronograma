from django.urls import path
from . import views

urlpatterns = [
    path('dashboard/', views.dashboard, name='notifications_dashboard'),
    path('ejecutar/', views.ejecutar_envios, name='ejecutar_envios'),
    path('sincronizar-cola/', views.sincronizar_cola_view, name='sincronizar_cola'),
    path('reenviar/<uuid:pk>/', views.reenviar_notificacion, name='reenviar_notificacion'),
    path('editar-reenviar/<uuid:pk>/', views.editar_reenviar, name='editar_reenviar'),
    path('cancelar/<uuid:pk>/', views.cancelar_notificacion, name='cancelar_notificacion'),
    path('masivo/', views.notificaciones_masivas, name='notificaciones_masivas'),
    path('generar-proyeccion/', views.generar_desde_proyeccion, name='generar_desde_proyeccion'),
    path('api/proyeccion/', views.api_get_proyeccion, name='api_proyeccion'),
    path('api/preview/', views.get_render_preview, name='get_render_preview'),
    
    # Nuevas rutas para el sistema manual
    path('api/recipient-candidates/', views.get_recipient_candidates, name='get_recipient_candidates'),
    path('api/send-manual/', views.send_manual_notifications, name='send_manual_notifications'),
    path('api/retry/<uuid:notification_id>/', views.retry_notification, name='retry_notification'),
    path('api/edit/<uuid:notification_id>/', views.edit_notification, name='edit_notification'),
    path('api/details/<uuid:notification_id>/', views.get_notification_details, name='get_notification_details'),
    path('api/filter-historial/', views.filter_historial, name='filter_historial'),
]


