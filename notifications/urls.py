from django.urls import path
from . import views

urlpatterns = [
    path('dashboard/', views.dashboard, name='notifications_dashboard'),
    path('ejecutar/', views.ejecutar_envios, name='ejecutar_envios'),
    path('notificar-inicio/', views.notificar_inicio, name='notificar_inicio'),
]
