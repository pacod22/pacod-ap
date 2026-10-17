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

# Slim deps: the chat API never runs the PDF pipeline (that is the ingestion
# service's job), so it installs requirements-base.txt and does NOT pull marker-pdf
# → torch/triton. The bundled ingestion package still imports fine (marker is a lazy,
# function-scoped import in ingestion/parser.py), so unit tests that import
# ingestion.splitter / sources.local_docs pass; only actual PDF conversion — which is
# never run in this container — would require marker.
COPY requirements-base.txt .
RUN pip install -r requirements-base.txt

# pacod-id's Python library (token checks, rules engine, client), vendored as a wheel by
# scripts/vendor-pacod-id.sh. Installed from its file with --no-index, never resolved
# through an index; its dependencies (httpx, PyJWT[crypto], PyYAML) are pinned above.
COPY vendor/wheels ./vendor/wheels
RUN pip install --no-deps --no-index vendor/wheels/pacod_id-*.whl

COPY app ./app
COPY ingestion ./ingestion
# Not imported on the api runtime path, but tests/unit import them — copy so
# `make test-unit` (run inside this image) can collect the full suite. Without
# admin_api the pre-existing admin tests abort collection and NO unit tests run.
COPY improvement_loop ./improvement_loop
COPY admin_api ./admin_api
COPY scripts ./scripts
COPY embeddings_service ./embeddings_service
COPY mcp_server ./mcp_server
COPY ingestion_api ./ingestion_api
# The app's pacod-id manifest (pushed as the `pacod` catalogue) and its test vectors.
COPY authz ./authz
COPY tests ./tests
# pytest.ini carries asyncio_mode=auto; without it every undecorated async test
# fails with "async def functions are not natively supported".
COPY pytest.ini ./
COPY data ./data
COPY documents ./documents

# Healthcheck endpoint.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
