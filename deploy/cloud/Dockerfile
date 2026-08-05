FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements-cloud.txt ./
RUN pip install --no-cache-dir -r requirements-cloud.txt

COPY app ./app
RUN mkdir -p /app/data && useradd --system --uid 10001 --create-home autodev \
    && chown -R autodev:autodev /app
USER autodev

EXPOSE 8765
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765", "--proxy-headers", "--forwarded-allow-ips", "*"]
