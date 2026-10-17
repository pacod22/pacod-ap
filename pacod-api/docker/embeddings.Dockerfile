FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Create the render group so /dev/dri/renderD* devices are accessible.
# GID 109 is the Debian/Ubuntu default when the amdgpu driver is installed.
# If the host uses a different GID, pass RENDER_GID in .env and align group_add
# in docker-compose accordingly.
RUN groupadd -g 109 render 2>/dev/null || true

WORKDIR /srv

# ROCm-enabled PyTorch for AMD GPUs.
# The wheel bundles the HIP runtime (libamdhip64, ROCBlas, MIOpen, etc.)
# so no separate OS-level ROCm installation is needed inside the container.
# The host must have the amdgpu kernel module loaded and expose /dev/kfd + /dev/dri.
# TORCH_INDEX_URL: the local overlay (docker-compose.local.yml) passes the CPU
# index — the ROCm index has no arm64 wheel, so an Apple-Silicon build fails.
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/rocm6.2
RUN pip install torch --index-url ${TORCH_INDEX_URL}

# FastAPI runtime + FlagEmbedding (bge-m3 embedder + bge-reranker-v2-m3).
# Models are downloaded from HuggingFace on first start (~2GB, then cached).
# Set EMBEDDINGS_BACKEND=hash to skip model loading in CI / lightweight dev.
RUN pip install \
    fastapi==0.139.0 \
    uvicorn[standard]==0.32.0 \
    pydantic==2.13.4 \
    prometheus-client==0.21.0 \
    "FlagEmbedding>=1.2.0,<1.3.0" \
    "transformers>=4.40.0,<4.47.0" \
    peft

# Optional ONNX Runtime reranker (RERANKER_BACKEND=onnx_int8, CPU path only).
# optimum exports/runs bge-reranker-v2-m3 as ONNX; onnxruntime is the CPU engine.
# Harmless when unused — the default FlagReranker path does not import these.
RUN pip install "optimum[onnxruntime]>=1.20.0,<2.0.0"

# The embeddings service is otherwise isolated from the `app` package; pull in
# only the shared Prometheus metrics module (and its empty package inits) so
# `from app.core.metrics import setup_metrics` resolves without dragging in the
# rest of app/core (config/db/services).
COPY app/__init__.py ./app/__init__.py
COPY app/core/__init__.py ./app/core/__init__.py
COPY app/core/metrics.py ./app/core/metrics.py

COPY embeddings_service ./embeddings_service

# start-period covers the one-time model load from HuggingFace cache (~60s).
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=5 \
    CMD curl -fsS http://localhost:8080/health || exit 1

EXPOSE 8080
CMD ["uvicorn", "embeddings_service.server:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]
