# Multi-stage build: Node compiles the React bundle, Python serves both the
# API and the bundle from a single FastAPI process.

# ---- Stage 1: build the frontend ----
FROM node:20-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
# --legacy-peer-deps matches the resolution mode used to generate the
# lockfile locally. Required because vite-plugin-pwa@1.2.0 doesn't list
# vite@8 as a supported peer; strict npm ci would otherwise fail. Revisit
# when vite-plugin-pwa publishes a vite-8-compatible release.
RUN npm ci --legacy-peer-deps
COPY frontend ./
RUN npm run build

# ---- Stage 2: Python runtime ----
FROM python:3.12-slim
WORKDIR /app

# Install Python deps via pyproject.toml (pip's PEP 517 build).
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -e .

# Copy backend source + project config.
COPY backend ./backend
COPY config.yaml ./

# Pull the built frontend bundle into the location the FastAPI app expects.
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Drop root before runtime. /tmp is world-writable so the SQLite DB at
# /tmp/sports_picks.db (per DATABASE_PATH env var) works without extra chown.
RUN useradd --create-home --shell /bin/bash --uid 1000 app \
    && chown -R app:app /app
USER app

# Fly's [http_service] internal_port=8000 matches the default fallback
# below. Bind to 0.0.0.0 so the proxy can route into the container.
ENV PYTHONUNBUFFERED=1
CMD ["sh", "-c", "uvicorn backend.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
