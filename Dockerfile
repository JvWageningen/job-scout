# syntax=docker/dockerfile:1

# Build stage: resolve and install dependencies into a self-contained venv.
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install dependencies before copying the source so this layer stays cached
# until the lockfile itself changes -- a source edit then rebuilds in seconds
# rather than re-resolving 100+ packages on slow NAS hardware.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev


# Runtime stage: no uv, no build cache, no dev dependencies.
FROM python:3.12-slim-bookworm

# tzdata is required: the scheduler resolves "Europe/Amsterdam" through
# zoneinfo, which reads the system timezone database, and without it a
# 17:00 slot would silently be interpreted as 17:00 UTC.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# uid 1000 matches the account that owns the bind-mounted directories on the
# NAS, so the container can write its database without running as root.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin scout \
    && mkdir -p /data \
    && chown 1000:1000 /data

COPY --from=builder --chown=1000:1000 /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    JOB_SCOUT_DATA_DIR=/data \
    TZ=Europe/Amsterdam

WORKDIR /app
USER scout

VOLUME ["/data"]

ENTRYPOINT ["job-scout"]

# The scheduler is the reason this runs on a NAS at all; the dashboard is a
# second service off the same image. Override with ["web", "--port", "..."].
CMD ["schedule", "loop", "--all"]
