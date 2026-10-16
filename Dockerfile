# uv only turns uv.lock into a hash-pinned requirements file (same version as CI).
# It is copied to /opt in the builder, which the production stage never copies.
FROM ghcr.io/astral-sh/uv:0.9.26 as uv

# Build stage
FROM python:3.12-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install the exact locked, hash-verified runtime dependencies (no dev extras).
# Done before the app code is copied so this layer is cached until the lock changes.
COPY --from=uv /uv /opt/uv/uv
COPY pyproject.toml uv.lock ./
RUN /opt/uv/uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.txt \
    && pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt

# Copy project files
COPY app/ app/
COPY config/ config/
COPY migrations/ migrations/
COPY scripts/ scripts/
COPY wiring.py .
COPY tasks.py .

# Install the project itself (editable, as before); its dependencies are already locked above
RUN pip install --no-cache-dir --no-deps -e .

# Production stage
FROM python:3.12-slim

WORKDIR /app

# ffmpeg: extract poster frames from uploaded project videos
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home --shell /bin/bash app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY --from=builder /app /app

# Set ownership
RUN chown -R app:app /app

# Switch to non-root user
USER app

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

# Run with gunicorn. gthread workers (4 x 4 threads) so a slow photo/video upload, an ffmpeg
# thumbnail or a PDF export holds one thread, not one of only four processes: with sync workers
# four slow requests blocked every other user. Same settings as the QA stack.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "4", "--worker-class", "gthread", "--threads", "4", "--timeout", "120", "app:create_app()"]
