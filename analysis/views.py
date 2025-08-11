from django.shortcuts import render, redirect
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.decorators import login_required

from django.contrib.auth.forms import AuthenticationForm

from django.contrib.auth.decorators import login_required
from django.core.files.storage import FileSystemStorage
from django.conf import settings
from .models import EcgProcess

from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404
from django.core.files.storage import default_storage

import requests
import io
import os
import zipfile
import tempfile
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


@login_required
def ecg_plot(request, pk):
    """
    Читает ZIP (.hea/.dat) через Django Storage (без абсолютных путей),
    строит превью ЭКГ и возвращает PNG.

    Параметры запроса (необязательные):
      - records_n: количество отображаемых каналов (по умолчанию 3)
      - sampling_rate: доля точек от полного сигнала (по умолчанию 1e-4)
    """
    obj = get_object_or_404(EcgProcess, pk=pk, user=request.user)

    # --- Отладочная инфа о путях и стораже ---
    try:
        print("=== ECG DEBUG START ===")
        print("MEDIA_ROOT:", settings.MEDIA_ROOT)
        print("MEDIA_URL:", settings.MEDIA_URL)
        print("ecg_file.name:", obj.ecg_file.name)  # относительный путь от MEDIA_ROOT, например "ecg_files/test001.zip")

        storage_path = None
        try:
            storage_path = default_storage.path(obj.ecg_file.name)
            print("default_storage.path(...):", storage_path)
            print("os.path.exists(storage_path):", os.path.exists(storage_path))
        except Exception as e:
            print("default_storage.path ERROR:", repr(e))

        # Не используем абсолютный путь — читаем через storage/open
        print("Will open via obj.ecg_file.open('rb') and stream bytes.")
        print("=== ECG DEBUG END ===")
    except Exception:
        # на случай, если print где-то упадёт — не ломаем вью
        pass

    name_lower = (obj.ecg_file.name or "").lower()
    if not name_lower.endswith(".zip"):
        raise Http404("Ожидается ZIP-файл (WFDB .hea/.dat внутри).")

    # Параметры визуализации
    try:
        records_n = int(request.GET.get('records_n', 3))
    except Exception:
        records_n = 3
    try:
        sampling_rate = float(request.GET.get('sampling_rate', 1e-4))
    except Exception:
        sampling_rate = 1e-4

    # Тяжёлые импорты — внутри функции
    import numpy as np
    import wfdb
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    # Читаем файл ЧЕРЕЗ storage, без абсолютных путей
    try:
        with obj.ecg_file.open('rb') as fobj:
            zip_bytes = io.BytesIO(fobj.read())
    except Exception as e:
        raise Http404(f"Не удалось открыть файл из storage: {e}")

    # Распаковываем во временную директорию и ищем пару hea/dat
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            with zipfile.ZipFile(zip_bytes, 'r') as zf:
                zf.extractall(tmpdir)
        except Exception as e:
            raise Http404(f"Некорректный ZIP: {e}")

        bases = {}
        for fname in os.listdir(tmpdir):
            base, ext = os.path.splitext(fname)
            ext = ext.lower()
            if ext in ('.hea', '.dat'):
                bases.setdefault(base, set()).add(ext)

        selected_base = None
        for base, exts in bases.items():
            if '.hea' in exts:  # требуем наличие заголовка
                selected_base = base
                break
        if not selected_base:
            raise Http404("В ZIP не найдены файлы WFDB (.hea/.dat).")

        fpath = os.path.join(tmpdir, selected_base)  # базовое имя без расширения
        try:
            record = wfdb.rdrecord(fpath)
        except Exception as e:
            raise Http404(f"Не удалось прочитать WFDB запись: {e}")

        if getattr(record, 'p_signal', None) is None:
            raise Http404("WFDB запись не содержит p_signal.")

        signals = record.p_signal.T  # (n_leads, n_samples)
        n_leads, total_samples = signals.shape

        records_n = max(1, min(records_n, n_leads))
        n_samples = int(total_samples * sampling_rate)
        n_samples = max(1, min(n_samples, total_samples))

        signals = signals[:records_n, :n_samples]

        # Рисуем
        fig, axes = plt.subplots(nrows=records_n, figsize=(10, 2.2 * records_n), sharex=True)
        if records_n == 1:
            axes = [axes]

        for idx in range(records_n):
            axes[idx].plot(signals[idx], linewidth=1.0)
            axes[idx].set_ylabel(f"Канал {idx+1}")
            axes[idx].grid(True, alpha=0.3)

        # axes[-1].set_xlabel("Отсчёты")
        fig.suptitle(f"ЭКГ превью")
        fig.tight_layout(rect=[0, 0, 1, 0.95])

        buf = io.BytesIO()
        fig.savefig(buf, format='png')
        plt.close(fig)
        buf.seek(0)
        return HttpResponse(buf.read(), content_type='image/png')
