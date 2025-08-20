# analysis/utils.py
import io
from typing import Optional, Sequence
import numpy as np


def _signals_to_mV(signals: np.ndarray, units: Optional[Sequence[str]]) -> np.ndarray:
    """Приводим все каналы к мВ (если units = V/µV). Если units неизвестны — считаем мВ."""
    out = np.array(signals, dtype=float, copy=True)
    if not units:
        return out
    scales = []
    for i in range(out.shape[0]):
        u = (units[i] if i < len(units) and units[i] else '').lower()
        if u in ('mv',):
            s = 1.0
        elif u in ('v',):
            s = 1000.0
        elif u in ('uv', 'µv'):
            s = 1.0 / 1000.0
        else:
            s = 1.0  # неизвестно — считаем мВ
        scales.append(s)
    return out * np.array(scales)[:, None]


def render_ecg_png(
    signals: np.ndarray,
    fs: float,
    title: str = "ЭКГ",
    lead_labels: Optional[Sequence[str]] = None,
    units: Optional[Sequence[str]] = None,
) -> io.BytesIO:
    """
    Рисует ECG с бумажной сеткой: 25 мм/с по X и 10 мм/мВ по Y.
    - По оси X цифр нет (время читается по миллиметровке).
    - Клетки квадратные, масштаб по амплитуде одинаков для всех отведений.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import math

    if signals.ndim != 2:
        raise ValueError("signals должен быть (n_leads, n_samples)")

    # Бумажные параметры
    paper_speed_mm_s = 25.0   # мм/с
    gain_mm_per_mV   = 20.0   # мм/мВ
    mm_per_V = gain_mm_per_mV * 1000.0

    # Время и амплитуда -> в миллиметры
    n_leads, n_samples = signals.shape
    t = np.arange(n_samples, dtype=float) / float(fs)       # секунды
    x_mm = t * paper_speed_mm_s                              # мм по X

    y_mV = _signals_to_mV(signals, units)                    # мВ
    y_mm_all = y_mV * gain_mm_per_mV                         # мм по Y

    # Общие Y-пределы для всех отведений (одинаковый масштаб)
    y_min_mm = float(np.min(y_mm_all)) if y_mm_all.size else -10.0
    y_max_mm = float(np.max(y_mm_all)) if y_mm_all.size else  10.0
    span = max(1e-6, y_max_mm - y_min_mm)
    pad = 0.2 * span
    y_lo = math.floor((y_min_mm - pad) / 1.0) * 1.0          # кратно 1 мм
    y_hi = math.ceil ((y_max_mm + pad) / 1.0) * 1.0

    # Точки сетки (1 мм — minor, 5 мм — major)
    x_end = float(x_mm[-1]) if x_mm.size else 0.0
    xt_minor = np.arange(0, x_end + 1.0, 1.0)
    xt_major = np.arange(0, x_end + 5.0, 5.0)
    yt_minor = np.arange(y_lo, y_hi + 1.0, 1.0)
    yt_major = np.arange(y_lo, y_hi + 5.0, 5.0)

    # Фигура
    fig, axes = plt.subplots(nrows=n_leads, figsize=(11, 2.4 * n_leads), sharex=False)
    if isinstance(axes, np.ndarray):
        axes = axes.ravel().tolist()
    else:
        axes = [axes]

    for i, ax in enumerate(axes):
        y_mm = y_mm_all[i]

        # Сетка
        ax.set_xticks(xt_major); ax.set_xticks(xt_minor, minor=True)
        ax.set_yticks(yt_major); ax.set_yticks(yt_minor, minor=True)
        ax.grid(which='major', color='#ffb3b3', linewidth=0.9, alpha=0.9)
        ax.grid(which='minor', color='#ffe6e6', linewidth=0.6, alpha=0.9)

        # Равный масштаб по X/Y в мм → квадратные клетки
        ax.set_aspect('equal', adjustable='box')

        # Сигнал в мм-координатах
        ax.plot(x_mm, y_mm, color='black', linewidth=1.1)

        # Пределы
        ax.set_xlim(0, x_end)
        ax.set_ylim(y_lo, y_hi)

        # Подпись отведения (без чисел на осях)
        label = (lead_labels[i] if lead_labels and i < len(lead_labels) else f"Канал {i+1}")
        ax.set_ylabel(label)
        ax.tick_params(axis='x', which='both', labelbottom=False)
        ax.tick_params(axis='y', which='both', labelleft=False)

    # Подписи с масштабом (без чисел на X)
    if axes:
        axes[-1].set_xlabel("Скорость 25 мм/с")  # текстовая пометка без числовой шкалы
    fig.text(0.005, 0.5, f"Усиление {gain_mm_per_mV:g} мм/мВ",
             va='center', rotation='vertical')

    # fig.suptitle(title)
    fig.tight_layout(rect=[0.02, 0.02, 1, 0.95])

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=200)
    plt.close(fig)
    buf.seek(0)
    return buf

