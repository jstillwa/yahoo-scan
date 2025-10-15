# Multi-stage build with official uv image
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (better caching)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Copy source and install project
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Final slim runtime image
FROM python:3.12-slim

WORKDIR /app

# Copy virtual environment and source code from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/inbox_cleaner /app/inbox_cleaner

# Set PATH to use venv
ENV PATH="/app/.venv/bin:${PATH}"

# Data dir for sqlite state
VOLUME ["/data"]

# Default command runs the CLI
# Pass --auto for automatic mode: docker run ... inbox-cleaner --auto
CMD ["inbox-cleaner"]
