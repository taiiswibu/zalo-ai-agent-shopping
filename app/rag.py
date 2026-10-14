"""Versioned, permission-gated RAG over existing knowledge table.

SQLite chunk and embedding cache. Gemini vector retrieval is optional; lexical
retrieval always works offline. No torch/FAISS/Chroma or server needed.
"""
from __future__ import annotations
import hashlib
import math
import re
from dataclasses import dataclass
from sqlalchemy import select, delete
from sqlalchemy.orm import Session
from app.models import Knowledge, RagChunk, RagVector
from app.retrieval import terms, score
from app.ai import embed
from app.config import settings

@dataclass
class Hit:
    key: str
    title: str
    text: str
    url: str
    score: float


def pieces(content: str, max_chars: int = 650, overlap: int = 100) -> list[str]:
    """Split around paragraphs, then long paragraphs into overlapping segments."""
    blocks = [p.strip() for p in re.split(r"\n\s*\n", content) if p.strip()]
    out: list[str] = []
    for block in blocks:
        while len(block) > max_chars:
            end = block.rfind(" ", max_chars // 2, max_chars + 1)
            end = end if end > 0 else max_chars
            section = block[:end].strip()
            if section: out.append(section)
            # limit overlap to avoid infinite loops when no spaces exist
            block = block[max(1, end - overlap):].lstrip()
        if block:
            if out and len(out[-1]) + len(block) + 2 <= max_chars:
                out[-1] += "\n\n" + block
            else:
                out.append(block)
    return out


def sync_index(db: Session, *, build_vectors: bool = False, max_new_vectors: int = 120) -> dict:
    """Incremental and idempotent: only approved knowledge enters the index."""
    count = embedded = 0
    documents = db.scalars(select(Knowledge)).all()
    for doc in documents:
        old = db.scalars(select(RagChunk).where(RagChunk.knowledge_id == doc.id)).all()
        digest = hashlib.sha256(doc.content.encode("utf-8")).hexdigest()
        stale = not doc.approved or any(c.content_hash != digest for c in old)
        if stale:
            ids = [c.id for c in old]
            if ids:
                db.execute(delete(RagVector).where(RagVector.chunk_id.in_(ids)))
                db.execute(delete(RagChunk).where(RagChunk.id.in_(ids)))
            old = []
        if not doc.approved:
            continue
        if not old:
            for no, part in enumerate(pieces(doc.title + "\n\n" + doc.content)):
                db.add(RagChunk(knowledge_id=doc.id, chunk_no=no, content_hash=digest, text=part))
                count += 1
        db.flush()
    db.commit()
    if build_vectors and settings.rag_backend == "gemini":
        approved_chunks = db.execute(
            select(RagChunk, Knowledge).join(Knowledge, Knowledge.id == RagChunk.knowledge_id)
            .where(Knowledge.approved.is_(True))
        ).all()
        for chunk, doc in approved_chunks:
            if embedded >= max_new_vectors:
                break
            existing = db.scalar(select(RagVector).where(
                RagVector.chunk_id == chunk.id, RagVector.model == settings.embedding_model))
            if existing:
                continue
            values = embed(chunk.text, task_type="RETRIEVAL_DOCUMENT")
            if values is None:
                break # quota/error: keep lexical retrieval
            db.add(RagVector(chunk_id=chunk.id, model=settings.embedding_model, vector=values))
            db.commit() # incremental, resumable if rate limit hits
            embedded += 1
    return {"new_chunks": count, "embedded": embedded}


def cosine(x: list[float], y: list[float]) -> float:
    if len(x) != len(y) or not x:
        return 0.0
    dot = sum(a * b for a, b in zip(x, y))
    nx = math.sqrt(sum(v*v for v in x))
    ny = math.sqrt(sum(v*v for v in y))
    return dot / (nx * ny) if nx and ny else 0.0


def search(db: Session, question: str, limit: int = 3) -> list[Hit]:
    sync_index(db, build_vectors=False)
    candidates = db.execute(
        select(RagChunk, Knowledge).join(Knowledge, Knowledge.id == RagChunk.knowledge_id)
        .where(Knowledge.approved.is_(True))
    ).all()
    vectors: dict[int, list[float]] = {}
    query_vec = None
    if candidates and settings.rag_backend == "gemini":
        stored = db.scalars(select(RagVector).where(RagVector.model == settings.embedding_model)).all()
        vectors = {v.chunk_id: v.vector for v in stored}
        if any(chunk.id in vectors for chunk, _ in candidates):
            query_vec = embed(question)
    ranked = []
    for chunk, doc in candidates:
        lex = max(score(question, chunk.text), score(question, doc.title) * 0.85)
        semantic = cosine(query_vec, vectors[chunk.id]) if query_vec and chunk.id in vectors else 0
        if lex <= 0 and semantic < 0.42:
            continue
        # Prefer semantic but preserve lexical exact terms and avoid irrelevant retrieval.
        combined = 0.4 * lex + 0.6 * max(semantic, 0)
        ranked.append(Hit(doc.source_key, doc.title, chunk.text[:560], doc.source_url, combined))
    ranked.sort(key=lambda h: (-h.score, h.key))
    return ranked[:limit]
