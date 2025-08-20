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
from .utils import render_ecg_png


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
    Читает ZIP (.hea/.dat) через Django Storage, извлекает ВСЁ из .hea
    и строит PNG с бумажной ECG-сеткой (25 мм/с, 10 мм/мВ).

    - НИКАКИХ параметров из GET не используем.
    - Показываем первые 10 секунд (если запись короче — всю запись).
    """
    obj = get_object_or_404(EcgProcess, pk=pk, user=request.user)

    name_lower = (obj.ecg_file.name or "").lower()
    if not name_lower.endswith(".zip"):
        raise Http404("Ожидается ZIP-файл (WFDB .hea/.dat внутри).")

    # --- читаем zip из storage ---
    try:
        with obj.ecg_file.open('rb') as fobj:
            zip_bytes = io.BytesIO(fobj.read())
    except Exception as e:
        raise Http404(f"Не удалось открыть файл из storage: {e}")

    import wfdb
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            with zipfile.ZipFile(zip_bytes, 'r') as zf:
                zf.extractall(tmpdir)
        except Exception as e:
            raise Http404(f"Некорректный ZIP: {e}")

        # ищем базовое имя с .hea
        bases = {}
        for fname in os.listdir(tmpdir):
            base, ext = os.path.splitext(fname)
            if ext.lower() in ('.hea', '.dat'):
                bases.setdefault(base, set()).add(ext.lower())

        selected_base = next((b for b, exts in bases.items() if '.hea' in exts), None)
        if not selected_base:
            raise Http404("В ZIP не найдены файлы WFDB (.hea/.dat).")

        fpath = os.path.join(tmpdir, selected_base)

        # читаем WFDB
        try:
            record = wfdb.rdrecord(fpath)
        except Exception as e:
            raise Http404(f"Не удалось прочитать WFDB запись: {e}")

        fs = getattr(record, 'fs', None)
        if not fs or fs <= 0:
            raise Http404("В заголовке .hea не указана валидная частота дискретизации (fs).")

        # берём физические единицы если есть, иначе цифровые
        if getattr(record, 'p_signal', None) is not None:
            sig = record.p_signal.T  # (leads, samples)
            sig_units = list(getattr(record, 'sig_units', []) or getattr(record, 'units', []) or [])
        elif getattr(record, 'd_signal', None) is not None:
            # физкалибровки может не быть — тогда сетка всё равно будет 10 мм/мВ,
            # но значения будут в "кодах". Стараемся привести к мВ при наличии units/gain.
            sig = record.d_signal.T.astype(float)
            sig_units = []
        else:
            raise Http404("WFDB запись не содержит p_signal/d_signal.")

        lead_labels = list(getattr(record, 'sig_name', []) or [])
        n_leads, total_samples = sig.shape

        # показываем первые 10 секунд
        seconds_to_show = 10.0
        n_samples = int(min(total_samples, max(1, fs * seconds_to_show)))
        sig = sig[:, :n_samples]

        # отрисовка
        buf = render_ecg_png(
            signals=sig,
            fs=fs,
            lead_labels=lead_labels,
            units=sig_units,
            title="ЭКГ"
        )
        return HttpResponse(buf.read(), content_type='image/png')


