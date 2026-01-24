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

# Copy requirements if you have them, or install directly
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn[standard] \
    redis \
    pydantic[email] \
    pyyaml \
    requests \
    python-multipart \
    openai \
    jsonschema \
    jinja2 \
    prometheus-client \
    boto3 \
    sqlalchemy \
    asyncpg \
    cryptography \
    aiohttp \
    httpx \
    structlog \
    weasyprint \
    sendgrid \
    bcrypt

# Copy application code
COPY . /app

# Create necessary directories
RUN mkdir -p /tmp/msp-demo-state /var/lib/msp/evidence /var/lib/mcp-server

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the full server with database
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
