# ---- API (FastAPI) — o frontend (Fase 2) será adicionado como estágio de build e copiado para frontend_dist
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    UPLOAD_DIR=/data/uploads ENVIRONMENT=production

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install -r requirements.txt

COPY backend/ .
RUN chmod +x start.sh && useradd -m app && mkdir -p /data/uploads && chown -R app /data /app
USER app

EXPOSE 8000
CMD ["./start.sh"]
