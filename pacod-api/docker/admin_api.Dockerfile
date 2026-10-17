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

# Slim deps: admin-api delegates ingestion to the ingestion service over HTTP and
# never runs the PDF pipeline in-process, so it must not pull marker-pdf → torch.
COPY requirements-base.txt .
RUN pip install -r requirements-base.txt

# pacod-id's Python library (token checks, rules engine, client), vendored as a wheel by
# scripts/vendor-pacod-id.sh. Installed from its file with --no-index, never resolved
# through an index; its dependencies (httpx, PyJWT[crypto], PyYAML) are pinned above.
COPY vendor/wheels ./vendor/wheels
RUN pip install --no-deps --no-index vendor/wheels/pacod_id-*.whl

COPY app ./app
COPY admin_api ./admin_api
# The app's pacod-id manifest: the admin API derives roles from it.
COPY authz ./authz

RUN mkdir -p /srv/documents

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

EXPOSE 8000
CMD ["uvicorn", "admin_api.main:app", "--host", "0.0.0.0", "--port", "8000"]