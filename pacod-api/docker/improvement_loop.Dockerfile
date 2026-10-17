FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# Reuses the main app's runtime deps (openai client, qdrant-client,
# sqlalchemy+asyncpg, redis, structlog, httpx, tenacity, pydantic).
# Slim deps: the loop never runs the PDF pipeline in-process (it reaches ingestion
# over HTTP), so it must not pull marker-pdf → torch/triton into this image.
COPY requirements-base.txt .
RUN pip install -r requirements-base.txt

# The loop imports app.core.* / app.services.* as a library — never app.main.
COPY app ./app
COPY improvement_loop ./improvement_loop

# A job container, not a server: no EXPOSE / HEALTHCHECK. Run via
#   docker compose run --rm improvement-loop run [--date YYYY-MM-DD] [--dry-run]
ENTRYPOINT ["python", "-m", "improvement_loop"]
CMD ["run"]
