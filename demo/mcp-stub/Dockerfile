FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir \
    fastapi==0.109.0 \
    uvicorn==0.27.0 \
    redis==5.0.1 \
    pydantic==2.5.3 \
    python-multipart==0.0.6 \
    httpx==0.26.0

COPY server.py .
COPY runbooks/ ./runbooks/

EXPOSE 8001

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]
