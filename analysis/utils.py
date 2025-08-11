# analysis/utils.py
import os
import zipfile
import tempfile
import pandas as pd
import wfdb

def read_wfdb_from_zip(zip_path: str, record_index: int = 1):
    """
    Читает запись WFDB (dat/hea) из ZIP-архива и возвращает (df, fs, record_name).
    df: pandas.DataFrame, колонки = имена отведений (если пустые -> Lead1..N)
    fs: float, частота дискретизации
    record_name: str, базовое имя записи в архиве
    """
    if record_index < 1:
        raise IndexError("record_index должен быть >= 1")

    with zipfile.ZipFile(zip_path, 'r') as archive:
        # все .hea — это отдельные записи
        record_names = sorted(set(f[:-4] for f in archive.namelist() if f.endswith('.hea')))
        if not record_names:
            raise FileNotFoundError("В архиве не найдено ни одного .hea файла")

        if record_index > len(record_names):
            raise IndexError(f"Номер записи должен быть от 1 до {len(record_names)}")

        record = record_names[record_index - 1]

        # Временная папка для распаковки пары .hea/.dat
        with tempfile.TemporaryDirectory() as tmpdir:
            base = os.path.join(tmpdir, os.path.basename(record))
            # распаковываем нужные файлы
            with open(base + ".hea", "wb") as f:
                f.write(archive.read(record + ".hea"))
            # dat может называться .dat или иметь номер/расширение, но чаще .dat
            # попробуем сначала .dat; если нет — найдём все файлы с тем же base и не .hea
            dat_written = False
            for cand in [record + ".dat"]:
                if cand in archive.namelist():
                    with open(base + ".dat", "wb") as f:
                        f.write(archive.read(cand))
                    dat_written = True
                    break

            if not dat_written:
                # fallback: вытащим все файлы, что начинаются с record и не .hea
                for name in archive.namelist():
                    if name.startswith(record) and not name.endswith(".hea"):
                        with open(os.path.join(tmpdir, os.path.basename(name)), "wb") as f:
                            f.write(archive.read(name))
                        dat_written = True
                if not dat_written:
                    raise FileNotFoundError("Не удалось найти .dat (или другие сигнальные) файлы для записи")

            # читаем WFDB
            signals, fields = wfdb.rdsamp(base)

    # Имена каналов
    sig_names = fields.get('sig_name') or []
    if (not sig_names) or all(s is None for s in sig_names):
        sig_names = [f"Lead{i+1}" for i in range(signals.shape[1])]

    df = pd.DataFrame(signals, columns=sig_names)
    fs = float(fields.get('fs', 0.0))
    record_name = os.path.basename(record)
    return df, fs, record_name
