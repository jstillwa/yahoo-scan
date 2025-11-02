# Multi-stage build with official uv image
FROM ghcr.io/astral-sh/uv:0.9.7-python3.14-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (better caching)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Copy source and install project (non-editable for Docker)
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# Final slim runtime image
FROM python:3.14-slim

WORKDIR /app

# Copy virtual environment from builder (package installed in site-packages)
COPY --from=builder /app/.venv /app/.venv

# Set PATH to use venv
ENV PATH="/app/.venv/bin:${PATH}"

# Data dir for sqlite state
VOLUME ["/data"]

# Default command runs the CLI
# Pass --auto for automatic mode: docker run ... inbox_cleaner --auto
CMD ["inbox_cleaner"]
