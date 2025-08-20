# analysis/utils.py
import io
from typing import Optional, Sequence
import numpy as np
import re
from math import gcd
from fractions import Fraction
from scipy.signal import resample_poly
import io, os, zipfile, tempfile
from django.core.files.uploadedfile import SimpleUploadedFile


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


def _sanitize_record_name(name: str) -> str:
    base = os.path.splitext(os.path.basename(name))[0]
    base = re.sub(r'[^A-Za-z0-9_\-]+', '_', base)
    return (base or "record")[:64]


def _lcm(a, b): return a * b // gcd(a, b)


def _choose_target_fs(fs_list, mode="max", set_fs=None):
    fs_list = [int(round(f)) for f in fs_list]
    if mode == "set":
        if not set_fs:
            raise ValueError("--fs must be set when mode='set'")
        return int(round(set_fs))
    if len(set(fs_list)) == 1:
        return fs_list[0]
    if mode == "lcm":
        t = fs_list[0]
        for f in fs_list[1:]:
            t = _lcm(t, f)
            if t > 5000:  # ограничим безумные LCM
                return max(fs_list)
        return t
    return max(fs_list)  # default


def convert_uploaded_edf_to_wfdb_zip(uploaded_edf_file, fs_mode="max", set_fs=None, seconds=None) -> SimpleUploadedFile:
    """
    Принимает загруженный EDF-файл (UploadedFile), возвращает SimpleUploadedFile (ZIP с .hea/.dat).
    """
    # Локальные импорты, чтобы зависимости тянулись только при EDF
    import pyedflib, wfdb

    recname = _sanitize_record_name(uploaded_edf_file.name)

    # Пишем EDF во временный файл (pyEDFlib требует путь)
    edf_bytes = uploaded_edf_file.read()
    with tempfile.TemporaryDirectory() as tmp:
        edf_path = os.path.join(tmp, "in.edf")
        with open(edf_path, "wb") as f:
            f.write(edf_bytes)

        # Чтение EDF (физические значения)
        r = pyedflib.EdfReader(edf_path)
        try:
            n = r.signals_in_file
            labels = r.getSignalLabels()
            fs_list = [int(r.getSampleFrequency(i)) for i in range(n)]
            units = [r.getPhysicalDimension(i) or "" for i in range(n)]
            sigs = [r.readSignal(i).astype(np.float64) for i in range(n)]
            sigs = [np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0) for x in sigs]
        finally:
            r.close()

        # Выбираем общий fs и ресемплим при необходимости
        fs_target = _choose_target_fs(fs_list, mode=fs_mode, set_fs=set_fs)
        resampled = []
        for x, fs_src in zip(sigs, fs_list):
            if fs_src == fs_target:
                y = x
            else:
                frac = Fraction(fs_target, fs_src).limit_denominator()
                y = resample_poly(x, frac.numerator, frac.denominator)
            resampled.append(y)
        min_len = min(len(y) for y in resampled)
        data = np.vstack([y[:min_len] for y in resampled])  # (n_signals, n_samples)

        if seconds and seconds > 0:
            n_samples = int(round(seconds * fs_target))
            n_samples = max(1, min(n_samples, data.shape[1]))
            data = data[:, :n_samples]

        # Пишем WFDB во временную папку с помощью wfdb.wrsamp
        outdir = tmp
        p_sig = data.T  # (n_samples, n_signals)
        fmt = ['16'] * p_sig.shape[1]
        if not labels or len(labels) != p_sig.shape[1]:
            labels = [f"ch{i+1}" for i in range(p_sig.shape[1])]
        if not units or len(units) != p_sig.shape[1]:
            units = [''] * p_sig.shape[1]

        wfdb.wrsamp(record_name=recname,
                    fs=fs_target,
                    units=units,
                    sig_name=labels,
                    p_signal=p_sig,
                    fmt=fmt,
                    comments=[f"Converted from EDF; original fs={fs_list}; target fs={fs_target}"],
                    write_dir=outdir)

        # Упаковываем .hea и .dat в ZIP (в память)
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for ext in ('.hea', '.dat'):
                path = os.path.join(outdir, recname + ext)
                zf.write(path, arcname=recname + ext)
        zip_buf.seek(0)

    # Возвращаем как загруженный ZIP для дальнейшей логики
    return SimpleUploadedFile(f"{recname}.zip", zip_buf.getvalue(), content_type='application/zip')
