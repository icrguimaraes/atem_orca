# ---- 1) Frontend (React + Vite)
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ .
RUN npm run build

# ---- 2) API (FastAPI) servindo a SPA
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    UPLOAD_DIR=/data/uploads ENVIRONMENT=production

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install -r requirements.txt

COPY backend/ .
COPY --from=web /web/dist ./frontend_dist
RUN chmod +x start.sh && useradd -m app && mkdir -p /data/uploads && chown -R app /data /app
USER app

EXPOSE 8000
CMD ["./start.sh"]
