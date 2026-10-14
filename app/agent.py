"""Backward compatible entry point: all existing Zalo/API code still imports respond."""
from __future__ import annotations
import logging
import time
from sqlalchemy.orm import Session
from app.commerce import AgentResult, money, price_ceiling, render_product
from app.models import AgentTrace
from app.advanced.flow import execute
from app.config import settings

log = logging.getLogger(__name__)


def respond(db: Session, chat_id: str, question: str) -> AgentResult:
    started = time.perf_counter()
    result, engine = execute(db, chat_id, question)
    try:
        db.add(AgentTrace(
            chat_id=chat_id, intent=result.intent, retrieval_keys=result.evidence,
            engine=engine, llm_provider=settings.llm_provider,
            latency_ms=round((time.perf_counter() - started) * 1000),
            status="ok"
        ))
        db.commit()
    except Exception as exc:
        db.rollback()
        log.warning("Unable to persist trace: %s", type(exc).__name__)
    return result
