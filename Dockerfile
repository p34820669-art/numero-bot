FROM python:3.12-slim

# Шрифт с кириллицей для PDF
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV PYTHONUNBUFFERED=1
# Хостинг сам задаёт PORT. База и PDF лежат в /app/data (на бесплатном тарифе сбрасываются при перезапуске).
CMD ["sh", "-c", "uvicorn web.server:app --host 0.0.0.0 --port ${PORT:-8765}"]
