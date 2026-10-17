FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    ca-certificates \
    libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# CPU-only PyTorch — excludes CUDA/ROCm kernels, ~1.5 GB smaller than the GPU wheel.
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

# FlagEmbedding for bge-m3 inline embedding + application runtime deps.
# marker-pdf and Surya are intentionally omitted — PDFs are extracted with
# pypdf (text-only) in the CPU pipeline to avoid loading 2-4 GB of layout
# models onto a memory-constrained host.
RUN pip install \
    fastapi==0.139.0 \
    uvicorn[standard]==0.32.0 \
    pydantic==2.13.4 \
    pydantic-settings==2.14.2 \
    python-multipart==0.0.32 \
    "FlagEmbedding>=1.2.0,<1.3.0" \
    "transformers>=4.40.0,<4.47.0" \
    peft \
    qdrant-client==1.12.1 \
    "sqlalchemy[asyncio]==2.0.36" \
    asyncpg==0.30.0 \
    redis[hiredis]==5.1.1 \
    structlog==24.4.0 \
    httpx==0.27.2 \
    tenacity==9.0.0 \
    pypdf==6.19.0 \
    python-docx==1.1.2 \
    beautifulsoup4==4.13.5 \
    lxml==6.1.3 \
    markdown==3.11 \
    lingua-language-detector==2.2.0 \
    prometheus-client==0.21.0

RUN mkdir -p /srv/documents

# Pre-download bge-m3 during image build so the first container start does
# not silently block for 3-10 minutes on a 2.3 GB HuggingFace download.
# It sits BEFORE the code is copied: a layer after a COPY is rebuilt whenever that code
# changes, which re-downloaded the model (and wrote another 2.3 GB layer) on every edit.
RUN python -c "\
from FlagEmbedding import BGEM3FlagModel; \
BGEM3FlagModel('BAAI/bge-m3', use_fp16=False, device='cpu'); \
print('bge-m3 pre-download OK')"

COPY app ./app
COPY ingestion ./ingestion
COPY ingestion_api ./ingestion_api
COPY scripts ./scripts
COPY data ./data

# EMBEDDINGS_BACKEND=inline_cpu: use in-process CpuEmbedder instead of the
# HTTP embeddings service.  INGEST_DOC_CONCURRENCY=1: sequential processing
# to keep memory predictable on constrained hosts.
ENV EMBEDDINGS_BACKEND=inline_cpu \
    INGEST_DOC_CONCURRENCY=1

# start-period covers model load from the pre-baked HuggingFace cache (~90 s
# on a 2-core CPU); shorter than the GPU pipeline's 120 s because there is
# no reranker to load.
HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

EXPOSE 8000
CMD ["uvicorn", "ingestion_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
