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
	Читает ZIP (.hea/.dat) через Django Storage, извлекает метаданные из .hea,
	строит превью ЭКГ; при известной скорости бумаги рисует ECG-сетку.

	Параметры GET (опционально):
	  - records_n: число отображаемых каналов (по умолчанию все из файла)
	  - sampling_rate: частота дискретизации, Гц (по умолчанию из файла)
	  - seconds: длительность отображаемого отрезка, с (по умолчанию 10)
	  - paper_speed: скорость бумаги, мм/с (если не задана и не найдена — сетка не рисуется)
	"""
	obj = get_object_or_404(EcgProcess, pk=pk, user=request.user)

	# --- Отладочная инфа о путях и стороже ---
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

	# Опциональные оверрайды из запроса
	q_records_n = request.GET.get('records_n')
	q_fs = request.GET.get('sampling_rate')
	q_seconds = request.GET.get('seconds')
	q_paper_speed = request.GET.get('paper_speed')
	try:
		q_records_n = int(q_records_n) if q_records_n is not None else None
	except Exception:
		q_records_n = None
	try:
		q_fs = float(q_fs) if q_fs is not None else None
	except Exception:
		q_fs = None
	try:
		q_seconds = float(q_seconds) if q_seconds is not None else None
	except Exception:
		q_seconds = None
	try:
		q_paper_speed = float(q_paper_speed) if q_paper_speed is not None else None
	except Exception:
		q_paper_speed = None

	# Импорт WFDB внутри функции
	import wfdb
	import re

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

		# Сигналы: предпочитаем p_signal (в физических единицах), иначе d_signal
		if getattr(record, 'p_signal', None) is not None:
			signals = record.p_signal.T
		elif getattr(record, 'd_signal', None) is not None:
			signals = record.d_signal.T.astype(float)
		else:
			raise Http404("WFDB запись не содержит p_signal/d_signal.")

		# Параметры из заголовка
		fs = getattr(record, 'fs', None)
		lead_labels = list(getattr(record, 'sig_name', []) or [])
		units = None
		if hasattr(record, 'units') and record.units:
			units = list(record.units)
		elif hasattr(record, 'sig_units') and record.sig_units:
			units = list(record.sig_units)

		# Попытка извлечь скорость из комментариев заголовка (форматы вида "25 mm/s")
		paper_speed = q_paper_speed
		if paper_speed is None and hasattr(record, 'comments') and record.comments:
			for comment in record.comments:
				m = re.search(r'(\d+(?:[\.,]\d+)?)\s*mm\s*/?\s*s', comment, flags=re.IGNORECASE)
				if not m:
					m = re.search(r'(\d+(?:[\.,]\d+)?)\s*мм\s*/?\s*с', comment, flags=re.IGNORECASE)
				if m:
					try:
						paper_speed = float(m.group(1).replace(',', '.'))
					except Exception:
						paper_speed = None
					break

		n_leads, total_samples = signals.shape

		# Значения по умолчанию на основе файла + возможные оверрайды из запроса
		records_n = q_records_n if q_records_n is not None else n_leads
		if q_fs is not None:
			fs = q_fs

		seconds = q_seconds if q_seconds is not None else 10.0
		if fs and fs > 0:
			n_samples = int(min(total_samples, max(1, fs * seconds)))
		else:
			# если fs неизвестна, ограничим разумным числом точек
			n_samples = min(total_samples, 5000)

		records_n = max(1, min(records_n, n_leads))
		signals = signals[:records_n, :n_samples]

		from .utils import render_ecg_png
		buf = render_ecg_png(
			signals,
			records_n=records_n,
			title="ЭКГ",
			lead_labels=lead_labels,
			fs=fs,
			units=units,
			paper_speed=paper_speed,
		)

		return HttpResponse(buf.read(), content_type='image/png')
