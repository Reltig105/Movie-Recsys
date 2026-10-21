# Обучающий образ LightFM (Linux + OpenMP).
#
# Зачем Docker: LightFM 1.17 — Cython-расширение 2020 года. На Windows его
# WARP/BPR падают с access violation (и в сборке из исходников, и в бинарнике
# conda-forge), а на Linux тот же код работает и использует все ядра через
# OpenMP. Артефакты (``.npy``) переносимы, поэтому обучение живёт в контейнере,
# а API — в обычном venv.
#
# Сборка и запуск (из корня проекта):
#
#   docker build -t rexter-train backend/ml_pipeline
#   docker run --rm -v "%CD%:/app" rexter-train \
#       python backend/ml_pipeline/train_lightfm.py --epochs 10
#
# LightFM 1.17 несовместим с numpy 2.x — фиксируем numpy 1.26 / scipy 1.13.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install "numpy==1.26.4" "scipy==1.13.1" \
    && pip install "setuptools<75" wheel \
    && pip install "lightfm==1.17" "faiss-cpu==1.15.1"

WORKDIR /app
