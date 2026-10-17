FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# Slim runtime deps only (openai client, sqlalchemy+asyncpg, redis,
# structlog, httpx, tenacity, pydantic, beautifulsoup4/lxml/feedparser, aiosmtplib).
# Deliberately requirements-base.txt, NOT requirements.txt: the worker does HTML/feed
# parsing + LLM classify + SMTP and never touches the PDF pipeline, so it must not
# pull marker-pdf → torch/triton (multi-GB) into this image.
COPY requirements-base.txt .
RUN pip install -r requirements-base.txt

# The worker imports app.core.* / app.services.* as a library — never app.main.
COPY app ./app
COPY newsletter_worker ./newsletter_worker

# A worker container, not a server: no EXPOSE / HEALTHCHECK. Run via
#   docker compose up -d newsletter-worker   (command: worker)
ENTRYPOINT ["python", "-m", "newsletter_worker"]
CMD ["worker"]
