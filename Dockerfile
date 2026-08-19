FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 1 воркер: у ньому живе фоновий планувальник добового скану і статус в памʼяті.
# Історія зрізів зберігається на диску (DATA_DIR) — під Railway підключити Volume.
ENV PORT=8080
CMD gunicorn app:app --workers 1 --threads 8 --timeout 120 --graceful-timeout 20 --access-logfile - --error-logfile - --bind 0.0.0.0:${PORT:-8080}
