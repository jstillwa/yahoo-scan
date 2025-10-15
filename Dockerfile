# Slim, reproducible Python with uv as the sole builder/installer
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install uv (single static binary)
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh

# Add uv to PATH
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

# Only copy manifests first for better caching
COPY pyproject.toml /app/

# Create a dedicated, deterministic venv and sync deps
RUN uv venv /app/.venv && . /app/.venv/bin/activate && uv sync --no-dev --frozen
ENV PATH="/app/.venv/bin:${PATH}"

# Now copy the code
COPY inbox_cleaner /app/inbox_cleaner

# Data dir for sqlite state
VOLUME ["/data"]

# Default command runs the CLI
# Pass --auto for automatic mode: docker run ... inbox-cleaner --auto
CMD ["inbox-cleaner"]
