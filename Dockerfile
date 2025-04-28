FROM python:3.10.9

RUN mkdir -p /home

#WORKDIR $DockerHOME
WORKDIR /home
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

RUN pip install --upgrade pip

#COPY . $DockerHOME
COPY . /home

RUN pip install -r requirements.txt

EXPOSE 8000