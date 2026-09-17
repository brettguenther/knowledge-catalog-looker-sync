FROM python:3.11-slim

# Install git and ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source and config
COPY pyproject.toml .
COPY main.py .
COPY src/ ./src/
COPY config/ ./config/

# Ensure default sync_config.yaml exists from template if not provided
RUN cp -n config/sync_config.yaml.example config/sync_config.yaml || true

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "main.py"]
CMD ["sync", "--config", "config/sync_config.yaml", "--no-deploy", "--pr"]


