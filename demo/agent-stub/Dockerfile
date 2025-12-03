FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir \
    httpx==0.26.0 \
    pydantic==2.5.3 \
    pynacl==1.5.0

COPY agent.py .

CMD ["python", "agent.py"]
