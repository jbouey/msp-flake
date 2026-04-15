FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for WeasyPrint
RUN apt-get update && apt-get install -y \
    curl \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Install pinned Python dependencies
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r /app/requirements.lock

# Copy application code
COPY . /app

# Create necessary directories
RUN mkdir -p /tmp/msp-demo-state /var/lib/msp/evidence /var/lib/mcp-server /app/secrets

# Create non-root user pinned to UID 1000 to match host bind-mount
# ownership (secrets/, runbooks/, evidence/ on VPS are all uid=1000).
# useradd -r would assign a system uid (<1000) and break reads on
# bind-mounted secrets.
RUN useradd -m -u 1000 -s /bin/false appuser && \
    chown -R appuser:appuser /app /tmp/msp-demo-state /var/lib/msp /var/lib/mcp-server

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run as non-root user
USER appuser

# Run the full server with database
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
