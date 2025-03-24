from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),  # Главная страница или приветствие
    path('login/', views.user_login, name='login'),
    path('logout/', views.user_logout, name='logout'),
    path('register/', views.register, name='register'),

    # Страница для загрузки ЭКГ
    path('upload/', views.ecg_upload, name='ecg_upload'),
    # Страница истории обработок
    path('history/', views.ecg_history, name='ecg_history'),
]