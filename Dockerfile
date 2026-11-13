# syntax=docker/dockerfile:1
FROM node:24-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AUDIT_DB=/app/data/audit.sqlite3
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock \
    && groupadd --gid 10001 audit \
    && useradd --uid 10001 --gid audit --no-create-home audit \
    && mkdir /app/data \
    && chown audit:audit /app/data
COPY backend/app ./backend/app
COPY --from=frontend /build/dist ./frontend/dist
USER 10001:10001
VOLUME ["/app/data"]
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/api/health', timeout=4).read()"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8090", "--workers", "1"]
