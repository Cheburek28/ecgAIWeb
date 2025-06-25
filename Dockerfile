FROM python:3.10.9

# Создаём рабочую директорию
WORKDIR /home

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Обновляем pip
RUN pip install --upgrade pip

# Копируем только requirements.txt — чтобы сохранить кэш при изменении кода
COPY requirements.txt /home

# Устанавливаем зависимости
RUN pip install -r requirements.txt

# Теперь копируем остальной код
COPY . /home

EXPOSE 8000

# (опционально) Укажи команду запуска
# CMD ["python3", "api.py"]