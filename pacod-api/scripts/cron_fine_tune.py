"""Overnight bi-encoder + cross-encoder fine-tuning loop.

Run nightly (e.g. via cron or a Docker scheduled task):

    python -m scripts.cron_fine_tune

What it does
------------
1. Fetch positive (relevance=1) feedback pairs from the last N days.
2. Fetch chunk texts from Qdrant by chunk_id.
3. 80/20 train/val split.
4. Mine hard negatives: ANN search per query, exclude known positives.
5. Fine-tune the bi-encoder with MultipleNegativesRankingLoss (MNRL).
6. Fine-tune the cross-encoder with teacher-score distillation (MSE).
7. Evaluate: MRR@10 and Hit@10 on validation pairs.
8. If MRR improved by >= threshold, re-embed the full Qdrant collection with
   the new bi-encoder, write vectors into a versioned collection, atomically
   swap the Qdrant alias, and call the embeddings service POST /reload.
9. Save model weights to MODELS_OUTPUT_DIR.

Required env vars (same .env as the main app):
  QDRANT_URL, RETRIEVAL_COLLECTION_NAME,
  EMBEDDINGS_URL (for hot-reload after swap),
  POSTGRES_* (read from app.core.config)

Optional tuning knobs (env vars):
  FINETUNE_LOOKBACK_DAYS   int  default 30
  FINETUNE_MIN_PAIRS       int  default 50   — abort if fewer positive pairs
  FINETUNE_BATCH_SIZE      int  default 64
  FINETUNE_EPOCHS          int  default 3
  FINETUNE_MRR_THRESHOLD   float default 0.01
  FINETUNE_HARD_NEG_COUNT  int  default 5    — negatives per query
  FINETUNE_STAGE1_K        int  default 30   — ANN pool for hard-neg mining
  MODELS_OUTPUT_DIR        str  default /srv/models

Model format note
-----------------
Training uses sentence-transformers. The saved checkpoint (SentenceTransformer
layout: modules.json + 1_Pooling/ + transformer weights) is passed verbatim
to the embeddings service POST /reload which loads it via BGEM3FlagModel.
BGEM3FlagModel calls AutoModel.from_pretrained() on the root directory and
only uses the dense CLS embedding path, so it loads successfully from a
SentenceTransformer save; sparse/colbert heads are re-initialised but unused.
Verify this round-trip on your hardware before trusting production deploys.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import random
import shutil
from pathlib import Path
from typing import NamedTuple

import httpx
import numpy as np
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("cron_fine_tune")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


LOOKBACK_DAYS = _env_int("FINETUNE_LOOKBACK_DAYS", 30)
MIN_PAIRS = _env_int("FINETUNE_MIN_PAIRS", 50)
BATCH_SIZE = _env_int("FINETUNE_BATCH_SIZE", 64)
EPOCHS = _env_int("FINETUNE_EPOCHS", 3)
MRR_THRESHOLD = _env_float("FINETUNE_MRR_THRESHOLD", 0.01)
HARD_NEG_COUNT = _env_int("FINETUNE_HARD_NEG_COUNT", 5)
STAGE1_K = _env_int("FINETUNE_STAGE1_K", 30)
MODELS_OUTPUT_DIR = Path(_env_str("MODELS_OUTPUT_DIR", "/srv/models"))
EMBEDDINGS_URL = _env_str("EMBEDDINGS_URL", "http://pacod-embeddings:8080")


def _get_qdrant_url() -> str:
    from app.core.config import get_settings

    return get_settings().qdrant_url


def _get_collection_name() -> str:
    from app.core.config import get_settings

    return get_settings().retrieval_collection_name


def _get_postgres_dsn() -> str:
    from app.core.config import get_settings

    return get_settings().postgres_dsn


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class FeedbackPair(NamedTuple):
    query: str
    chunk_id: str
    source_id: str


class TrainingTriplet(NamedTuple):
    query: str
    positive: str
    negative: str


# ---------------------------------------------------------------------------
# DB: load feedback pairs
# ---------------------------------------------------------------------------


async def load_positive_pairs(lookback_days: int) -> list[FeedbackPair]:
    engine = create_async_engine(_get_postgres_dsn(), pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    since = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=lookback_days)
    async with Session() as session:
        rows = await session.execute(
            text(
                "SELECT query, chunk_id, source_id FROM retrieval_feedback "
                "WHERE relevance = 1 AND created_at >= :since"
            ),
            {"since": since},
        )
        pairs = [FeedbackPair(r.query, r.chunk_id, r.source_id) for r in rows]
    await engine.dispose()
    log.info("loaded_feedback_pairs count=%d lookback_days=%d", len(pairs), lookback_days)
    return pairs


# ---------------------------------------------------------------------------
# Qdrant helpers
# ---------------------------------------------------------------------------


async def fetch_chunk_texts(
    client: AsyncQdrantClient,
    collection: str,
    chunk_ids: list[str],
) -> dict[str, str]:
    if not chunk_ids:
        return {}
    result = await client.retrieve(
        collection_name=collection,
        ids=chunk_ids,
        with_payload=True,
    )
    return {str(p.id): (p.payload or {}).get("text", "") for p in result}


async def scroll_all_points(
    client: AsyncQdrantClient,
    collection: str,
) -> list[tuple[str, list[float], dict]]:
    """Return (id, vector, payload) for every point in the collection."""
    points: list[tuple[str, list[float], dict]] = []
    offset = None
    while True:
        batch, next_offset = await client.scroll(
            collection_name=collection,
            limit=256,
            offset=offset,
            with_vectors=True,
            with_payload=True,
        )
        for p in batch:
            if p.vector is not None:
                vec = p.vector if isinstance(p.vector, list) else list(p.vector)
                points.append((str(p.id), vec, p.payload or {}))
        if next_offset is None:
            break
        offset = next_offset
    log.info("scrolled_collection count=%d", len(points))
    return points


# ---------------------------------------------------------------------------
# Hard negative mining via ANN
# ---------------------------------------------------------------------------


async def mine_hard_negatives(
    client: AsyncQdrantClient,
    collection: str,
    pairs: list[FeedbackPair],
    chunk_texts: dict[str, str],
    biencoder,  # SentenceTransformer
    stage1_k: int,
    hard_neg_count: int,
) -> list[TrainingTriplet]:
    positive_ids_per_query: dict[str, set[str]] = {}
    for p in pairs:
        positive_ids_per_query.setdefault(p.query, set()).add(p.chunk_id)

    queries = list(positive_ids_per_query.keys())
    log.info("mining_hard_negatives queries=%d", len(queries))

    loop = asyncio.get_event_loop()
    q_vecs_np: np.ndarray = await loop.run_in_executor(
        None, lambda: biencoder.encode(queries, normalize_embeddings=True)
    )

    triplets: list[TrainingTriplet] = []
    for pair in pairs:
        pos_text = chunk_texts.get(pair.chunk_id, "")
        if not pos_text:
            continue

        query_idx = queries.index(pair.query)
        qvec = q_vecs_np[query_idx].tolist()

        results = await client.search(
            collection_name=collection,
            query_vector=qvec,
            limit=stage1_k + hard_neg_count,
        )

        known_positives = positive_ids_per_query[pair.query]
        neg_texts: list[str] = []
        for r in results:
            if str(r.id) not in known_positives:
                neg_text = (r.payload or {}).get("text", "")
                if neg_text:
                    neg_texts.append(neg_text)
                if len(neg_texts) >= hard_neg_count:
                    break

        for neg_text in neg_texts:
            triplets.append(TrainingTriplet(pair.query, pos_text, neg_text))

    log.info("mined_triplets count=%d", len(triplets))
    return triplets


# ---------------------------------------------------------------------------
# Evaluation: MRR@10 and Hit@10
# ---------------------------------------------------------------------------


def evaluate_retrieval(
    val_pairs: list[FeedbackPair],
    chunk_texts: dict[str, str],
    biencoder,  # SentenceTransformer
    all_points: list[tuple[str, list[float], dict]],
    top_k: int = 10,
) -> dict[str, float]:
    """In-memory MRR@top_k and Hit@top_k.

    Embeds validation queries with `biencoder` and ranks the existing index
    vectors by cosine similarity (dot-product on L2-normalised embeddings).
    The index vectors come from the current Qdrant collection and represent
    the OLD embeddings; this deliberately measures whether the new query-side
    model improves ranking given the same document representations.
    """
    if not val_pairs or not all_points:
        return {"mrr": 0.0, "hit": 0.0}

    queries = [p.query for p in val_pairs]
    q_vecs = biencoder.encode(queries, normalize_embeddings=True)  # (N, D)

    point_ids = [p[0] for p in all_points]
    c_vecs = np.array([p[1] for p in all_points])  # (M, D)
    scores_matrix: np.ndarray = q_vecs @ c_vecs.T  # (N, M)

    mrr_sum = 0.0
    hit_sum = 0.0
    for i, pair in enumerate(val_pairs):
        row = scores_matrix[i]
        top_indices = np.argpartition(row, -top_k)[-top_k:]
        top_indices = top_indices[np.argsort(row[top_indices])[::-1]]
        ranked_ids = [point_ids[j] for j in top_indices]
        if pair.chunk_id in ranked_ids:
            rank = ranked_ids.index(pair.chunk_id) + 1
            mrr_sum += 1.0 / rank
            hit_sum += 1.0

    n = len(val_pairs)
    return {"mrr": mrr_sum / n, "hit": hit_sum / n}


# ---------------------------------------------------------------------------
# Re-embed and upsert into versioned collection
# ---------------------------------------------------------------------------


async def reembed_collection(
    client: AsyncQdrantClient,
    old_collection: str,
    new_collection: str,
    biencoder,  # SentenceTransformer
    embed_batch: int = 8,
    upsert_batch: int = 32,
) -> None:
    existing = await client.get_collections()
    if new_collection in {c.name for c in existing.collections}:
        log.info("dropping_existing_versioned_collection name=%s", new_collection)
        await client.delete_collection(new_collection)

    old_info = await client.get_collection(old_collection)
    vector_size = old_info.config.params.vectors.size  # type: ignore[union-attr]
    await client.create_collection(
        collection_name=new_collection,
        vectors_config=qm.VectorParams(size=vector_size, distance=qm.Distance.COSINE),
    )
    for field, schema in (
        ("legal_year", qm.PayloadSchemaType.INTEGER),
        ("language", qm.PayloadSchemaType.KEYWORD),
        ("source_type", qm.PayloadSchemaType.KEYWORD),
        ("source_id", qm.PayloadSchemaType.KEYWORD),
    ):
        await client.create_payload_index(
            collection_name=new_collection,
            field_name=field,
            field_schema=schema,
        )

    all_points = await scroll_all_points(client, old_collection)
    log.info("reembedding count=%d", len(all_points))

    loop = asyncio.get_event_loop()
    ids = [p[0] for p in all_points]
    texts = [(p[2] or {}).get("text", "") for p in all_points]
    payloads = [p[2] for p in all_points]

    all_vectors: list[list[float]] = []
    for i in range(0, len(texts), embed_batch):
        batch_texts = texts[i : i + embed_batch]
        vecs = await loop.run_in_executor(
            None,
            lambda bt=batch_texts: biencoder.encode(bt, normalize_embeddings=True).tolist(),
        )
        all_vectors.extend(vecs)

    for i in range(0, len(ids), upsert_batch):
        points = [
            qm.PointStruct(id=cid, vector=vec, payload=payload)
            for cid, vec, payload in zip(
                ids[i : i + upsert_batch],
                all_vectors[i : i + upsert_batch],
                payloads[i : i + upsert_batch],
                strict=True,
            )
        ]
        await client.upsert(collection_name=new_collection, points=points)

    log.info("reembed_complete versioned_collection=%s", new_collection)


# ---------------------------------------------------------------------------
# Atomic alias swap
# ---------------------------------------------------------------------------


async def swap_alias(
    client: AsyncQdrantClient,
    alias_name: str,
    new_collection: str,
) -> None:
    await client.update_collection_aliases(
        change_aliases_operations=[
            qm.DeleteAliasOperation(delete_alias=qm.DeleteAlias(alias_name=alias_name)),
            qm.CreateAliasOperation(
                create_alias=qm.CreateAlias(
                    collection_name=new_collection,
                    alias_name=alias_name,
                )
            ),
        ]
    )
    log.info("alias_swapped alias=%s -> collection=%s", alias_name, new_collection)


# ---------------------------------------------------------------------------
# Embeddings service reload
# ---------------------------------------------------------------------------


async def reload_embeddings_service(model_path: str) -> None:
    async with httpx.AsyncClient(timeout=300.0) as http:
        resp = await http.post(
            f"{EMBEDDINGS_URL}/reload",
            json={"biencoder_path": model_path},
        )
        resp.raise_for_status()
    log.info("embeddings_service_reloaded path=%s", model_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    from app.core.logging import configure_logging

    configure_logging()

    qdrant_url = _get_qdrant_url()
    collection = _get_collection_name()

    # 1. Load feedback.
    pairs = await load_positive_pairs(LOOKBACK_DAYS)
    if len(pairs) < MIN_PAIRS:
        log.warning("insufficient_feedback pairs=%d min=%d — aborting", len(pairs), MIN_PAIRS)
        return

    # 2. Fetch chunk texts from Qdrant.
    client = AsyncQdrantClient(url=qdrant_url, prefer_grpc=False)
    unique_chunk_ids = list({p.chunk_id for p in pairs})
    chunk_texts = await fetch_chunk_texts(client, collection, unique_chunk_ids)
    pairs = [p for p in pairs if chunk_texts.get(p.chunk_id)]
    if len(pairs) < MIN_PAIRS:
        log.warning("pairs_after_text_fetch pairs=%d min=%d — aborting", len(pairs), MIN_PAIRS)
        await client.close()
        return

    # 3. Train/val split.
    random.shuffle(pairs)  # type: ignore[arg-type]
    split = max(1, int(len(pairs) * 0.8))
    train_pairs, val_pairs = pairs[:split], pairs[split:]
    log.info("split train=%d val=%d", len(train_pairs), len(val_pairs))

    # 4. Load baseline bi-encoder (sentence-transformers wrapper around bge-m3).
    log.info("loading_biencoder")
    from datasets import Dataset
    from sentence_transformers import InputExample, SentenceTransformer
    from sentence_transformers.losses import MultipleNegativesRankingLoss
    from sentence_transformers.trainer import SentenceTransformerTrainer
    from sentence_transformers.training_args import SentenceTransformerTrainingArguments

    biencoder_base = SentenceTransformer("BAAI/bge-m3")

    # 5. Mine hard negatives using the base model.
    triplets = await mine_hard_negatives(
        client,
        collection,
        train_pairs,
        chunk_texts,
        biencoder_base,
        STAGE1_K,
        HARD_NEG_COUNT,
    )
    if not triplets:
        log.warning("no_triplets_mined — aborting")
        await client.close()
        return

    # 6. Bi-encoder fine-tuning with MNRL.
    #    MNRL uses in-batch negatives so only (anchor, positive) pairs are needed.
    log.info(
        "biencoder_finetune start triplets=%d epochs=%d batch=%d",
        len(triplets),
        EPOCHS,
        BATCH_SIZE,
    )
    biencoder_ft = SentenceTransformer("BAAI/bge-m3")
    mnrl_loss = MultipleNegativesRankingLoss(model=biencoder_ft)

    train_dataset = Dataset.from_dict(
        {
            "anchor": [t.query for t in triplets],
            "positive": [t.positive for t in triplets],
        }
    )

    biencoder_output = MODELS_OUTPUT_DIR / "biencoder_ft"
    biencoder_output.mkdir(parents=True, exist_ok=True)

    training_args = SentenceTransformerTrainingArguments(
        output_dir=str(biencoder_output),
        num_train_epochs=EPOCHS,
        per_device_train_batch_size=BATCH_SIZE,
        warmup_ratio=0.1,
        fp16=False,
        logging_steps=50,
        save_strategy="epoch",
        report_to="none",
    )
    trainer = SentenceTransformerTrainer(
        model=biencoder_ft,
        args=training_args,
        train_dataset=train_dataset,
        loss=mnrl_loss,
    )
    trainer.train()
    ft_model_path = biencoder_output / "final"
    biencoder_ft.save(str(ft_model_path))
    log.info("biencoder_finetune_complete saved=%s", ft_model_path)

    # 7. Cross-encoder fine-tuning via teacher-score distillation.
    #    Build (query, doc, teacher_score) examples: the fine-tuned bi-encoder
    #    scores are used as targets. CrossEncoder.fit() uses MSELoss by default
    #    when labels are floats, which is equivalent to MarginMSE when the CE
    #    is trained to reproduce bi-encoder margins.
    log.info("cross_encoder_finetune start triplets=%d", len(triplets))
    from sentence_transformers import CrossEncoder
    from torch.utils.data import DataLoader

    loop = asyncio.get_event_loop()
    q_vecs = await loop.run_in_executor(
        None, lambda: biencoder_ft.encode([t.query for t in triplets], normalize_embeddings=True)
    )
    pos_vecs = await loop.run_in_executor(
        None, lambda: biencoder_ft.encode([t.positive for t in triplets], normalize_embeddings=True)
    )
    neg_vecs = await loop.run_in_executor(
        None, lambda: biencoder_ft.encode([t.negative for t in triplets], normalize_embeddings=True)
    )

    pos_scores = [float(np.dot(q, p)) for q, p in zip(q_vecs, pos_vecs, strict=False)]
    neg_scores = [float(np.dot(q, n)) for q, n in zip(q_vecs, neg_vecs, strict=False)]

    # Create flat (query, doc, label) examples where label = teacher bi-encoder score.
    ce_examples = []
    for t, ps, ns in zip(triplets, pos_scores, neg_scores, strict=False):
        ce_examples.append(InputExample(texts=[t.query, t.positive], label=ps))
        ce_examples.append(InputExample(texts=[t.query, t.negative], label=ns))

    ce_output = MODELS_OUTPUT_DIR / "crossencoder_ft"
    ce_output.mkdir(parents=True, exist_ok=True)

    ce_model = CrossEncoder("BAAI/bge-reranker-v2-m3", num_labels=1)
    ce_loader = DataLoader(ce_examples, batch_size=BATCH_SIZE, shuffle=True)
    ce_model.fit(
        train_dataloader=ce_loader,
        epochs=1,
        output_path=str(ce_output / "final"),
        show_progress_bar=True,
    )
    log.info("cross_encoder_finetune_complete saved=%s", ce_output / "final")

    # 8. Evaluate: compare fine-tuned bi-encoder against baseline on val set.
    log.info("evaluating val_size=%d", len(val_pairs))
    all_points = await scroll_all_points(client, collection)

    baseline_metrics = evaluate_retrieval(val_pairs, chunk_texts, biencoder_base, all_points)
    ft_metrics = evaluate_retrieval(val_pairs, chunk_texts, biencoder_ft, all_points)

    log.info(
        "eval_results baseline_mrr=%.4f baseline_hit=%.4f ft_mrr=%.4f ft_hit=%.4f",
        baseline_metrics["mrr"],
        baseline_metrics["hit"],
        ft_metrics["mrr"],
        ft_metrics["hit"],
    )

    mrr_delta = ft_metrics["mrr"] - baseline_metrics["mrr"]
    if mrr_delta < MRR_THRESHOLD:
        log.info(
            "improvement_below_threshold delta=%.4f threshold=%.4f — skipping deploy",
            mrr_delta,
            MRR_THRESHOLD,
        )
        await client.close()
        return

    log.info("deploying_fine_tuned_model mrr_delta=%.4f", mrr_delta)

    # 9. Re-embed the full collection into a versioned collection.
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
    versioned_collection = f"{collection}_v{timestamp}"
    await reembed_collection(client, collection, versioned_collection, biencoder_ft)

    # 10. Atomic alias swap.
    await swap_alias(client, alias_name=collection, new_collection=versioned_collection)

    # 11. Write weights to shared volume and hot-reload embeddings service.
    active_path = MODELS_OUTPUT_DIR / "biencoder_active"
    if active_path.exists():
        shutil.rmtree(active_path)
    shutil.copytree(str(ft_model_path), str(active_path))
    log.info("model_written_to_volume path=%s", active_path)

    await reload_embeddings_service(str(active_path))

    await client.close()
    log.info("fine_tune_complete versioned_collection=%s", versioned_collection)


if __name__ == "__main__":
    asyncio.run(main())
