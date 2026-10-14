# TeleMirror Dockerfile
# Optimized for Render, Railway, Fly.io, and Linux VPS

FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=10000 \
    WEB_HOST=0.0.0.0

# Set working directory
WORKDIR /app

# Install system dependencies (including OpenSSL for Telethon encryption)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Expose server port
EXPOSE 10000

# Health check endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:10000/healthz || exit 1

# Start TeleMirror server
CMD ["python", "main.py"]
