# analysis/utils.py
import io
from typing import Optional, Sequence
import numpy as np


def render_ecg_png(
	signals: np.ndarray,
	records_n: int = 3,
	title: str = "ЭКГ",
	lead_labels: Optional[Sequence[str]] = None,
	fs: Optional[float] = None,
	units: Optional[Sequence[str]] = None,
	paper_speed: Optional[float] = None,
) -> io.BytesIO:
	"""
	Рисует превью ЭКГ; при известной скорости бумаги рисует ECG-сетку.
	signals: np.ndarray формы (n_leads, n_samples) (в физ. единицах)
	records_n: сколько каналов показывать (1..n_leads)
	lead_labels: подписи для каналов (длина >= records_n), опционально
	fs: частота дискретизации (Гц) для оси X
	units: единицы измерения каналов (для подписи), опционально
	paper_speed: скорость бумаги (мм/с). Если None — сетка не рисуется.
	"""
	import matplotlib
	matplotlib.use('Agg')  # без GUI
	import matplotlib.pyplot as plt
	from matplotlib.ticker import MaxNLocator, MultipleLocator

	if signals.ndim != 2:
		raise ValueError("signals должен быть (n_leads, n_samples)")

	n_leads, _ = signals.shape
	records_n = max(1, min(records_n, n_leads))
	show = signals[:records_n]

	# X: время в секундах, если известен fs
	n_samples = show.shape[1]
	if fs and fs > 0:
		x = np.arange(n_samples) / float(fs)
		x_label = "s"
	else:
		x = np.arange(n_samples)
		x_label = "samples"

	fig, axes = plt.subplots(
		nrows=records_n, figsize=(11, 2.4 * records_n), sharex=True
	)
	if records_n == 1:
		axes = [axes]

	grid_enabled = (paper_speed is not None) and (fs is not None and fs > 0)

	# Сетка по времени: при известной бумажной скорости — крупная 5 мм и малая 1 мм
	for idx in range(records_n):
		ax = axes[idx]

		if grid_enabled and x.size > 0:
			major_xtick = 5.0 / float(paper_speed)
			minor_xtick = 1.0 / float(paper_speed)
			ax.set_xticks(np.arange(0, x[-1] + major_xtick, major_xtick))
			ax.set_xticks(np.arange(0, x[-1] + minor_xtick, minor_xtick), minor=True)

			# Y: подберём шаг 0.5 mV (major) и 0.1 mV (minor), если единицы mV
			label_units = None
			if units and idx < len(units):
				label_units = units[idx]

			if label_units and label_units.lower() == 'mv':
				data_min, data_max = np.min(show[idx]), np.max(show[idx])
				span = max(1e-6, data_max - data_min)
				pad = 0.2 * span
				y_low = data_min - pad
				y_high = data_max + pad
				major_ytick = 0.5
				minor_ytick = 0.1
				start_major = np.floor(y_low / major_ytick) * major_ytick
				start_minor = np.floor(y_low / minor_ytick) * minor_ytick
				ax.set_yticks(np.arange(start_major, y_high + major_ytick, major_ytick))
				ax.set_yticks(np.arange(start_minor, y_high + minor_ytick, minor_ytick), minor=True)
			else:
				ax.yaxis.set_major_locator(MaxNLocator(6))
				ax.yaxis.set_minor_locator(MultipleLocator(1))

			# styling grid
			ax.grid(which='major', color='#ffb3b3', linestyle='-', linewidth=0.8, alpha=0.7)
			ax.grid(which='minor', color='#ffe6e6', linestyle='-', linewidth=0.6, alpha=0.8)

		ax.plot(x, show[idx], color='black', linewidth=1.1)
		label = (
			lead_labels[idx] if lead_labels and idx < len(lead_labels)
			else f"Канал {idx+1}"
		)
		ax.set_ylabel(label)
		if fs and fs > 0 and x.size > 0:
			ax.set_xlim(0, x[-1])

		# скрыть числовые подписи на осях, оставить только сетку
		ax.tick_params(axis='x', which='both', labelbottom=False)
		ax.tick_params(axis='y', which='both', labelleft=False)

	# убрать подпись оси X
	axes[-1].set_xlabel("")
	# заголовок: добавим скорость, если она известна
	final_title = f"{title} • {paper_speed:g} мм/с" if paper_speed is not None else title
	fig.suptitle(final_title)
	fig.tight_layout(rect=[0, 0, 1, 0.95])

	buf = io.BytesIO()
	fig.savefig(buf, format='png')
	plt.close(fig)
	buf.seek(0)
	return buf
