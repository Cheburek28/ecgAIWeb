from django.shortcuts import render, redirect
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.decorators import login_required

from django.contrib.auth.forms import AuthenticationForm

from django.contrib.auth.decorators import login_required
from django.core.files.storage import FileSystemStorage
from django.conf import settings
from .models import EcgProcess

import requests
from django.contrib import messages


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

            # Проверяем что файл это zip
            if not ecg_file.name.endswith('.zip'):
                messages.error(request, "Можно загрузить только файлы в формате .zip")
                return redirect('ecg_upload')  # или можно перерендерить ту же страницу

            # Сохраняем модель
            ecg_process = EcgProcess.objects.create(
                user=request.user,
                ecg_file=ecg_file,
                comment=comment
            )

            # Делаем запрос к ecg_service
            try:
                ecg_service_url = getattr(settings, 'ECG_SERVICE_URL', 'http://localhost:8000')
                ecg_file.seek(0)
                response = requests.post(
                    f"{ecg_service_url}/ecg",
                    files={'file': (ecg_file.name, ecg_file.file, 'application/zip')}
                )
                response.raise_for_status()
                data = response.json()

                ecg_process.result = data
                # допустим, результат — это текст в ответе
                if "error" in data and data["error"]:
                    ecg_process.result = "Ошибка работы модели: " + data["error"]
                else:
                    ecg_process.result = data

            except requests.RequestException as e:
                try:
                    error_detail = e.response.json()
                except Exception:
                    error_detail = str(e)
                ecg_process.result = f"Ошибка обращения к ECG сервису: {error_detail}"

            ecg_process.save()

            return redirect('ecg_history')
        else:
            # Обработка случая, если файл не прикрепили
            pass
    return render(request, 'analysis/ecg_upload.html')


import markdown


@login_required
def ecg_history(request):
    # Получим все записи пользователя, отсортируем по дате
    ecg_records = EcgProcess.objects.filter(user=request.user).order_by('-created_at')

    for record in ecg_records:
        record.result_html = markdown.markdown(record.result, extensions=['extra'], output_format='html5')

    return render(request, 'analysis/ecg_history.html', {'ecg_records': ecg_records})
