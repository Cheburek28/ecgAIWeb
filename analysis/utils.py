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
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    import math

    if signals.ndim != 2:
        raise ValueError("signals должен быть (n_leads, n_samples)")

    # Бумага
    paper_speed_mm_s = 25.0     # мм/с
    gain_mm_per_mV   = 20.0     # мм/мВ  (как просил)
    MIN_SPAN_MM      = 40.0     # ⬅️ минимальная высота видимого окна по Y (±20 мм = ±1 мВ)
    mm_per_V         = gain_mm_per_mV * 1000.0

    # Координаты в мм
    n_leads, n_samples = signals.shape
    t = np.arange(n_samples, dtype=float) / float(fs)     # сек
    x_mm = t * paper_speed_mm_s                            # мм
    y_mV = _signals_to_mV(signals, units)                  # мВ
    y_mV = _signals_to_mV(signals, units)  # shape: (n_leads, n_samples)

    # авто-усиление
    BASE_GAIN = 20.0  # мм/мВ по умолчанию
    TARGET_PP_MM = 20.0  # хотим минимум 20 мм пик-ту-пик
    GAIN_CHOICES = [5, 10, 20, 40, 80, 160]  # стандартные ступени
    MAX_GAIN = 160.0

    # робастный пик-ту-пик по всем каналам
    pp_mV = []
    for i in range(y_mV.shape[0]):
        if y_mV.shape[1]:
            p1, p99 = np.percentile(y_mV[i], [1, 99])
            pp_mV.append(max(0.0, float(p99 - p1)))
    global_pp_mV = max(pp_mV) if pp_mV else 0.0

    # требуемая чувствительность, чтобы уложиться в TARGET_PP_MM
    if global_pp_mV > 0:
        need_gain = TARGET_PP_MM / global_pp_mV
    else:
        need_gain = BASE_GAIN

    gain_mm_per_mV = max(BASE_GAIN, min(MAX_GAIN, need_gain))

    # округлим до ближайшей "стандартной" вверх
    gain_mm_per_mV = next((g for g in GAIN_CHOICES if g >= gain_mm_per_mV), GAIN_CHOICES[-1])

    # далее как было:
    y_mm_all = y_mV * gain_mm_per_mV

    # Время по X
    x_end = float(x_mm[-1]) if x_mm.size else 0.0
    xt_minor = np.arange(0, x_end + 1.0, 1.0)              # 1 мм
    xt_major = np.arange(0, x_end + 5.0, 5.0)              # 5 мм

    # --- КЛЮЧ: единый минимальный диапазон по Y и робастная оценка размаха ---
    robust_spans = []
    for i in range(n_leads):
        y = y_mm_all[i]
        if y.size:
            p1, p99 = np.percentile(y, [1, 99])
            robust_spans.append((p99 - p1) * 1.2)          # небольшой запас
        else:
            robust_spans.append(0.0)
    global_span_mm = max(MIN_SPAN_MM, max(robust_spans) if robust_spans else MIN_SPAN_MM)

    # Подберём высоту фигуры так, чтобы клетки оставались квадратными и дорожки не были плоскими
    width_in = 11.0
    # Для equal: высота оси ≈ ширина оси * (global_span_mm / x_end)
    # Учтём поля: осевая ширина ~ 0.9*width_in
    h_per_row_in = max(1.2, (0.9 * width_in) * (global_span_mm / max(1e-6, x_end)))
    fig_height_in = h_per_row_in * n_leads

    fig, axes = plt.subplots(nrows=n_leads, figsize=(width_in, fig_height_in), sharex=False)
    if isinstance(axes, np.ndarray):
        axes = axes.ravel().tolist()
    else:
        axes = [axes]

    for i, ax in enumerate(axes):
        y = y_mm_all[i] if i < y_mm_all.shape[0] else np.array([])
        # Центр по медиане, одинаковый span для всех
        if y.size:
            y_center = float(np.median(y))
        else:
            y_center = 0.0
        y_lo = y_center - global_span_mm / 2.0
        y_hi = y_center + global_span_mm / 2.0
        # Доцелло до сетки 1 мм
        y_lo = math.floor(y_lo)
        y_hi = math.ceil (y_hi)

        # Сетка
        ax.set_xticks(xt_major); ax.set_xticks(xt_minor, minor=True)
        ax.set_yticks(np.arange(y_lo, y_hi + 5.0, 5.0))
        ax.set_yticks(np.arange(y_lo, y_hi + 1.0, 1.0), minor=True)
        ax.grid(which='major', color='#ffb3b3', linewidth=0.9, alpha=0.9)
        ax.grid(which='minor', color='#ffe6e6', linewidth=0.6, alpha=0.9)

        # Квадратные клетки
        ax.set_aspect('equal', adjustable='box')

        # Сигнал
        ax.plot(x_mm, y, color='black', linewidth=1.1)
        ax.set_xlim(0, x_end)
        ax.set_ylim(y_lo, y_hi)

        # Подписи
        label = (lead_labels[i] if lead_labels and i < len(lead_labels) else f"Канал {i+1}")
        ax.set_ylabel(label)
        ax.tick_params(axis='x', which='both', labelbottom=False)
        ax.tick_params(axis='y', which='both', labelleft=False)

    if axes:
        axes[-1].set_xlabel("Скорость 25 мм/с")
    fig.text(0.005, 0.5, f"Чувствительность {gain_mm_per_mV:g} мм/мВ",
             va='center', rotation='vertical')

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


def convert_uploaded_json_to_wfdb_zip(uploaded_json_file, fs=500) -> SimpleUploadedFile:
    """
    Конвертация JSON с signal_segments/dots → WFDB ZIP (.hea/.dat)
    """
    import json
    import numpy as np
    import wfdb
    import tempfile
    import os
    import io
    import zipfile

    recname = _sanitize_record_name(uploaded_json_file.name)

    payload = json.loads(uploaded_json_file.read().decode("utf-8"))

    signals = []
    labels = []

    for ch in payload:
        label = ch.get("label", "ch")
        scale = ch.get("scale", 1.0)

        segments = ch.get("signal_segments", [])
        dots = next((s for s in segments if s.get("type") == "dots"), None)
        if dots is None:
            continue

        data = dots.get("data", [])
        if not data:
            continue

        # data = [[index, value], ...]
        data = sorted(data, key=lambda x: x[0])

        values = np.array([v for _, v in data], dtype=np.float64)

        # применяем scale (если нужно — легко убрать)
        values = values / scale

        values = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)

        signals.append(values)
        labels.append(label)

    if not signals:
        raise ValueError("JSON does not contain valid ECG signals")

    # приводим к одинаковой длине
    min_len = min(len(s) for s in signals)
    signals = [s[:min_len] for s in signals]

    data = np.vstack(signals)  # (n_channels, n_samples)

    # --- пишем WFDB ---
    with tempfile.TemporaryDirectory() as tmp:
        p_signal = data.T  # (n_samples, n_channels)

        wfdb.wrsamp(
            record_name=recname,
            fs=fs,
            units=["mV"] * p_signal.shape[1],
            sig_name=labels,
            p_signal=p_signal,
            fmt=["16"] * p_signal.shape[1],
            comments=["Converted from JSON (dots format)"],
            write_dir=tmp
        )

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for ext in (".hea", ".dat"):
                zf.write(
                    os.path.join(tmp, recname + ext),
                    arcname=recname + ext
                )

        zip_buf.seek(0)

    return SimpleUploadedFile(
        f"{recname}.zip",
        zip_buf.getvalue(),
        content_type="application/zip"
    )
