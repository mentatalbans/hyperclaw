# HyperClaw — Production Dockerfile
# Base: python:3.11-slim
FROM python:3.11-slim

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

# Install package + deps
RUN pip install --no-cache-dir -e ".[dev]" && \
    pip install --no-cache-dir fastapi uvicorn[standard] redis

# Expose the API port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command: run the FastAPI server
CMD ["python3", "server.py"]
