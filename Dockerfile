# ─────────────────────────────────────────────────────────────
#  AI Autoposting Agent — Dockerfile
#  Multi-stage build: keeps the final image lean
# ─────────────────────────────────────────────────────────────

# Stage 1 — Builder
# Install Python deps in an isolated layer so they're cached
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build tools needed for some Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy and install requirements first (layer caching — only rebuilds if requirements.txt changes)
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --prefix=/install --no-cache-dir -r requirements.txt


# ─────────────────────────────────────────────────────────────
# Stage 2 — Runtime image
FROM python:3.11-slim

LABEL maintainer="AI Autoposting Agent"
LABEL description="AI-powered TikTok content automation"

# Install FFmpeg and other runtime system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder stage
COPY --from=builder /install /usr/local

# Create app user (never run as root in production)
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Create required directories with correct ownership
RUN mkdir -p \
    uploads \
    output/clips \
    output/thumbnails \
    output/store \
    watch_inbox \
    watch_processed \
    static \
    && chown -R appuser:appuser /app

# Copy application code
COPY --chown=appuser:appuser app/ ./app/
COPY --chown=appuser:appuser static/ ./static/
COPY --chown=appuser:appuser cli.py run.py setup_check.py ./

# Switch to non-root user
USER appuser

# Expose the app port
EXPOSE 8000

# Health check — hits the root endpoint every 30s
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

# Start the app
CMD ["python", "run.py"]
