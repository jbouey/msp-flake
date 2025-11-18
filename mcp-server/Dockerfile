FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements if you have them, or install directly
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn[standard] \
    redis \
    pydantic \
    pyyaml \
    requests \
    python-multipart \
    openai \
    jsonschema \
    jinja2 \
    prometheus-client \
    boto3

# Copy application code
COPY . /app

# Create necessary directories
RUN mkdir -p /tmp/msp-demo-state /var/lib/msp/evidence

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the minimal server for demo
CMD ["uvicorn", "server_minimal:app", "--host", "0.0.0.0", "--port", "8000"]
