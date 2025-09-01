FROM python:3.12-slim

# Prevent Python from writing .pyc files, force stdout/stderr unbuffered
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system packages (tzdata is useful for pytz / APScheduler)
RUN apt-get update -y && apt-get install -y --no-install-recommends \
    ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app code
COPY . /app

# Create dirs for SQLite + exports (mounted volume will be /data)
RUN mkdir -p /data /app/exports

# Environment defaults (can be overridden in Fly secrets/env)
ENV DB_PATH=/data/auto_sam.db

# Run the bot (polling)
CMD ["python", "bot.py"]
