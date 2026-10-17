FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt requirements-base.txt ./
RUN pip install -r requirements.txt

COPY app ./app
COPY ingestion ./ingestion
COPY ingestion_api ./ingestion_api
COPY scripts ./scripts
COPY data ./data

RUN mkdir -p /srv/documents

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

EXPOSE 8000
CMD ["uvicorn", "ingestion_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
