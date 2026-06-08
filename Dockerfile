# ─────────────────────────────────────────────────────────────────────────────
# Dockerfile — SharkExchange Trading Bot
# Base: Python 3.11 slim (smaller image, compatible with all deps)
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# Metadata
LABEL maintainer="tradingbot"
LABEL description="5-Minute BB Reversal Trading Bot — sharkexchange.in"

# Prevent Python from writing .pyc files / buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Working directory inside container
WORKDIR /app

# Install OS dependencies (needed by some Python packages)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy entire project
COPY . .

# Create a directory for logs so they persist via volume mount
RUN mkdir -p /app/logs

# Expose the Flask dashboard port
EXPOSE 5000

# Health check — pings the dashboard every 30 seconds
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:5000/ || exit 1

# Default entry point
CMD ["python", "run.py"]
