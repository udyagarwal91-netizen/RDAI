# For online hosting (Railway / Render). On a laptop you don't need this file — use the start files.
# This image already contains the hidden browser the picture maker needs (Playwright 1.64.0, Ubuntu 24.04).
FROM mcr.microsoft.com/playwright/python:v1.64.0-noble

ENV PYTHONUNBUFFERED=1 TZ=Asia/Kolkata DATA_DIR=/data LOGS_DIR=/data/logs ENV_FILE=/data/.env BRANDS_DIR=/data/brands
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "run.py"]
