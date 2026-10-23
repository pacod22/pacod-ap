"""Ingestion service entry point.

Standalone FastAPI service that handles all document ingestion:
  - Single file uploads  (POST /ingest/file)
  - URL ingestion        (POST /ingest/url)
  - Full corpus runs     (POST /ingest/corpus)
  - Collection reset     (POST /drop)

The service can also be used as a script host:
  docker compose exec ingestion python -m scripts.ingest_corpus
  docker compose exec ingestion python -m scripts.drop_collection
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.logging import configure_logging, get_logger
from app.core.metrics import setup_metrics
from ingestion_api.routes import router

configure_logging("ap-ingestion")
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("ingestion_service_startup")
    yield
    log.info("ingestion_service_shutdown")


app = FastAPI(
    title="Bulgarian Accounting Agent — Ingestion Service",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

setup_metrics(app, "ingestion")

app.include_router(router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "ingestion"}
