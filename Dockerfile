# ---- build stage -------------------------------------------------
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependency layer first: it changes rarely, so Docker caches it across
# every application code change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- runtime stage -----------------------------------------------
FROM python:3.12-slim AS runtime

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Never run as root: a container escape should not land on uid 0.
RUN groupadd -r grind && useradd -r -g grind -d /app grind

WORKDIR /app

COPY --from=builder --chown=grind:grind /app/.venv /app/.venv
COPY --chown=grind:grind app ./app
COPY --chown=grind:grind migrations ./migrations
# deploy must be copied as a directory: with a directory source, COPY takes
# its *contents*, which would put gunicorn.conf.py at /app/ and leave the
# CMD's deploy/gunicorn.conf.py path missing.
COPY --chown=grind:grind alembic.ini ./
COPY --chown=grind:grind deploy ./deploy

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER grind
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["gunicorn", "app.main:app", "-c", "deploy/gunicorn.conf.py"]
