# ---------- Stage 1: build Vite/Tailwind assets ----------
FROM node:20-slim AS assets
WORKDIR /app/vite
COPY vite/package.json vite/package-lock.json ./
RUN npm ci
COPY vite/ ./
# Tailwind (vite/assets/css/main.css) scans the Django templates via
# `@source "../../../src"`, so the app source must exist at /app/src during the
# build — otherwise Tailwind finds no classes and emits a near-empty stylesheet
# (JS works but the site renders completely unstyled).
COPY src /app/src
RUN npm run build                      # -> /app/vite/static (incl. manifest.json)

# ---------- Stage 2: Python runtime ----------
FROM python:3.13-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /uvx /bin/
# UV_COMPILE_BYTECODE_TIMEOUT: uv's per-file bytecode timeout is 60s. numpy
# (pulled in by pgvector) ships ~1 MB test modules that blow past it on the 1 GB
# droplet — worse because BuildKit runs the assets stage's `npm run build` on the
# same CPU. 0 = no timeout; compilation still completes, it just takes longer.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_COMPILE_BYTECODE_TIMEOUT=0 \
    UV_LINK_MODE=copy \
    DEBUG=False \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

# psycopg2-binary and Pillow ship manylinux wheels, so python:3.13-slim needs no extra
# OS packages. (If a future dep has no wheel, add: build-essential libpq-dev.)

# Install Python deps first so this layer caches across source-only changes
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# App source + the assets built in stage 1
COPY src ./src
COPY --from=assets /app/vite/static ./vite/static
# The admin reads these small manifests at runtime to discover article fonts.
# Keep the source font folders beside the production bundle so its choices
# match exactly what Vite built in the assets stage.
COPY --from=assets /app/vite/assets/fonts ./vite/assets/fonts
RUN uv sync --frozen --no-dev

# Bake static files into the image (WhiteNoise serves them; needs no DB or AWS creds)
RUN python src/main.py collectstatic --noinput

EXPOSE 8000
# Default command = web (1 worker, sized for the 1 GB droplet).
# docker-compose overrides `command` for the worker / scheduler / release services.
CMD ["gunicorn", "config.asgi:application", "-k", "uvicorn_worker.UvicornWorker", \
     "--bind", "0.0.0.0:8000", "--workers", "1", "--chdir", "src", \
     "--access-logfile", "-", "--log-level", "warning"]
