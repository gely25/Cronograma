from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('upload/', views.upload_excel, name='upload_excel'),
    path('cronograma/', views.ver_cronograma, name='ver_cronograma'),
]
