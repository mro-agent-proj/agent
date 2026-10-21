"""Local multilingual E5 encoding; the only model download is public model weights."""

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import numpy as np
from sqlalchemy import func, insert, select, text, update

from . import db
from .ingest import insert_batches, json_hash, require

MODEL = "intfloat/multilingual-e5-small"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
DIMENSION = 384
CONFIG = {
    "model": MODEL,
    "revision": REVISION,
    "dimension": DIMENSION,
    "distance": "cosine",
    "query_prefix": "query: ",
    "document_prefix": "passage: ",
    "normalize": True,
    "window_tokens": 480,
    "stride_tokens": 448,
    "window_pooling": "mean_then_normalize",
    "search_algorithm": "exact",
    "implementation": "e5-v1",
}
MODEL_VERSION = f"{MODEL}@{REVISION}"


class LocalEncoder:
    def __init__(self, device=None):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(
            MODEL,
            revision=REVISION,
            trust_remote_code=False,
            device=device or os.environ.get("MRO_EMBED_DEVICE", "cpu"),
        )
        require(self.model.get_sentence_embedding_dimension() == DIMENSION, "Model dimension mismatch")

    def encode(self, texts, *, query=False, batch_size=32):
        """Window overlong inputs explicitly; never silently discard the tail."""
        prefix = CONFIG["query_prefix"] if query else CONFIG["document_prefix"]
        windows, groups = [], []
        for value in texts:
            require(bool(value.strip()), "Empty embedding input")
            ids = self.model.tokenizer.encode(value, add_special_tokens=False)
            start = len(windows)
            if len(ids) <= CONFIG["window_tokens"]:
                windows.append(prefix + value)
            else:
                for offset in range(0, len(ids), CONFIG["stride_tokens"]):
                    windows.append(
                        prefix
                        + self.model.tokenizer.decode(
                            ids[offset : offset + CONFIG["window_tokens"]], skip_special_tokens=True
                        )
                    )
                    if offset + CONFIG["window_tokens"] >= len(ids):
                        break
            groups.append((start, len(windows)))
        lengths = [len(ids) for ids in self.model.tokenizer(windows, truncation=False)["input_ids"]]
        require(
            max(lengths, default=0) <= self.model.max_seq_length,
            "Retokenized window exceeds model limit; rebuild with a smaller versioned window",
        )
        encoded = self.model.encode(
            windows,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        vectors = np.array([encoded[a:b].mean(axis=0) for a, b in groups], dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        require(np.isfinite(vectors).all() and bool((norms > 0).all()), "Nonfinite/zero embedding")
        return vectors / norms


async def build_embeddings(engine, release_key, encoder=None, batch_size=64):
    """Build off the request path. A single transaction publishes only complete data."""
    encoder = encoder or await asyncio.to_thread(LocalEncoder)
    config_hash = json_hash(CONFIG)
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": release_key + config_hash},
        )
        release = (
            (await conn.execute(select(db.releases).where(db.releases.c.release_key == release_key)))
            .mappings()
            .one()
        )
        require(release["state"] == "READY", "Release not ready")
        index_key = f"IDX-{release_key}-e5-{config_hash[:12]}"
        old = (
            (await conn.execute(select(db.indexes).where(db.indexes.c.index_key == index_key)))
            .mappings()
            .first()
        )
        if old:
            require(old["state"] == "READY", "Index already exists but is not ready")
            return {"index_key": index_key, "reused": True}
        rows = (
            (
                await conn.execute(
                    select(db.chunks)
                    .where(db.chunks.c.release_id == release["id"])
                    .order_by(db.chunks.c.chunk_id)
                )
            )
            .mappings()
            .all()
        )
        require(bool(rows), "No chunks to embed")
        now, iid = datetime.now(UTC), uuid4()
        await conn.execute(
            insert(db.indexes).values(
                id=iid,
                release_id=release["id"],
                index_key=index_key,
                dataset="derived_amm",
                method="VECTOR",
                state="BUILDING",
                config_hash=config_hash,
                config=CONFIG,
                embedding_model_version=MODEL_VERSION,
                embedding_dimension=DIMENSION,
                storage_kind="PGVECTOR",
                storage_locator="rag.amm_embeddings",
                created_at=now,
            )
        )
        # Hash deduplication reuses computation, not source/chunk identity.
        unique = {r["content_hash"]: r["embedding_text"] for r in rows}
        hashes = sorted(unique)
        vectors = {}
        for start in range(0, len(hashes), batch_size):
            batch = hashes[start : start + batch_size]
            result = await asyncio.to_thread(encoder.encode, [unique[h] for h in batch])
            for h, value in zip(batch, result, strict=True):
                vectors[h] = value
            if start % (batch_size * 10) == 0:
                print(
                    f"Embedded {min(start + batch_size, len(hashes))}/{len(hashes)} unique inputs", flush=True
                )
        await insert_batches(
            conn,
            db.embeddings,
            [
                {
                    "id": uuid4(),
                    "release_id": release["id"],
                    "index_uuid": iid,
                    "chunk_uuid": r["id"],
                    "content_hash": r["content_hash"],
                    "embedding_model_version": MODEL_VERSION,
                    "dimension": DIMENSION,
                    "embedding": vectors[r["content_hash"]],
                    "created_at": now,
                }
                for r in rows
            ],
        )
        count = await conn.scalar(
            select(func.count()).select_from(db.embeddings).where(db.embeddings.c.index_uuid == iid)
        )
        require(count == len(rows), "Incomplete embedding coverage")
        await conn.execute(
            update(db.indexes)
            .where(db.indexes.c.id == iid)
            .values(state="READY", verified_at=datetime.now(UTC))
        )
    return {"index_key": index_key, "chunks": len(rows), "unique_inputs": len(hashes), "reused": False}
