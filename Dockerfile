# HyperClaw — Production Dockerfile
# Base: python:3.11-slim
FROM python:3.11-slim

RUN addgroup --system --gid 1001 app && adduser --system --uid 1001 --ingroup app app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set workdir
WORKDIR /app

# Copy pyproject first for layer caching
COPY pyproject.toml ./
COPY README.md ./

# Copy all source
COPY . .

# Install package + deps, then hand ownership to app user
RUN pip install --no-cache-dir . && \
    mkdir -p /app/data && \
    chown -R app:app /app

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER 1001

ENV HOST=0.0.0.0 PORT=8001 HYPERCLAW_ROOT=/app/data

# Expose the API port
EXPOSE 8001

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:8001/health || exit 1

# Default command: run the FastAPI server
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "run_hyperclaw.py"]
