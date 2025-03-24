from django.shortcuts import render, redirect
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.decorators import login_required

from django.contrib.auth.forms import AuthenticationForm

from django.contrib.auth.decorators import login_required
from django.core.files.storage import FileSystemStorage
from .models import EcgProcess


def register(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            # Автоматически логиним сразу после регистрации (по желанию)
            login(request, user)
            return redirect('home')
    else:
        form = UserCreationForm()
    return render(request, 'analysis/register.html', {'form': form})


def user_login(request):
    if request.method == 'POST':
        form = AuthenticationForm(data=request.POST)
        if form.is_valid():
            user = form.get_user()
            if user is not None:
                login(request, user)
                return redirect('home')
    else:
        form = AuthenticationForm()
    return render(request, 'analysis/login.html', {'form': form})


def user_logout(request):
    logout(request)
    return redirect('home')


def home(request):
    return render(request, 'analysis/home.html')


@login_required
def ecg_upload(request):
    if request.method == 'POST':
        comment = request.POST.get('comment', '')
        if 'ecg_file' in request.FILES:
            ecg_file = request.FILES['ecg_file']

            # Сохраняем модель
            ecg_process = EcgProcess.objects.create(
                user=request.user,
                ecg_file=ecg_file,
                comment=comment
            )
            # Имитируем длительную обработку (например, создаём задание для Celery
            # либо сразу здесь что-то делаем). В MVP можно просто записать "Обработка завершена".
            # ecg_process.result = run_ml_process(ecg_file)  # здесь вызов ml
            ecg_process.result = "Обработка завершена (заглушка)."
            ecg_process.save()

            return redirect('ecg_history')
        else:
            # Обработка случая, если файл не прикрепили
            pass
    return render(request, 'analysis/ecg_upload.html')


@login_required
def ecg_history(request):
    # Получим все записи пользователя, отсортируем по дате
    ecg_records = EcgProcess.objects.filter(user=request.user).order_by('-created_at')
    return render(request, 'analysis/ecg_history.html', {'ecg_records': ecg_records})
