from __future__ import annotations
import hmac
import logging
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.config import settings
from app.db import init_db, get_session
from app.models import ChatEvent, Product, Knowledge, Order
from app.seed import load_demo
from app.agent import respond
from app.zalo import verify_secret, parse_update, ZaloClient

log = logging.getLogger("zalo_bot")
logging.basicConfig(level=logging.INFO)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    # SQLite for local development; cloud requires external PostgreSQL (ephemeral container disks).
    if settings.database_url.startswith("sqlite") and settings.bot_mode == "live" and __import__("os").environ.get("CONTAINER_APP_NAME"):
        raise RuntimeError("Không được dùng SQLite trên Azure scale-to-zero; hãy đặt DATABASE_URL Postgres")
    init_db()
    if __import__("os").environ.get("SEED_DEMO", "false").lower() == "true":
        with next(get_session()) as db:
            load_demo(db)
    yield

app = FastAPI(title="Zalo AI Agent Free", version="1.0.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)

class TestChat(BaseModel):
    chat_id: str = Field(default="demo-user", min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=1000)

class ApprovePayload(BaseModel):
    source_key: str = Field(min_length=1, max_length=255)


def admin_auth(x_admin_token: str | None = Header(default=None)):
    if not settings.admin_token or settings.admin_token.startswith("replace-") or not x_admin_token or not hmac.compare_digest(settings.admin_token, x_admin_token):
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/readyz")
def readyz(db: Session = Depends(get_session)):
    db.execute(select(func.count()).select_from(Product)).scalar_one()
    return {"status": "ready"}

@app.post("/webhooks/zalo")
def webhook(payload: dict, x_bot_api_secret_token: str | None = Header(default=None), db: Session = Depends(get_session)):
    if not verify_secret(x_bot_api_secret_token):
        raise HTTPException(status_code=403, detail="Invalid bot webhook secret")
    event = parse_update(payload)
    if not event:
        return {"ok": True, "ignored": True}
    existing = db.scalar(select(ChatEvent).where(ChatEvent.provider_event_id == event["event_id"]))
    if existing and existing.status == "sent":
        return {"ok": True, "duplicate": True}
    if existing and existing.status == "processing":
        age = (datetime.now(timezone.utc) - (existing.created_at.replace(tzinfo=timezone.utc)
              if existing.created_at.tzinfo is None else existing.created_at)).total_seconds()
        if age < 120:
            return {"ok": True, "duplicate": True}
    if not existing:
        record = ChatEvent(provider_event_id=event["event_id"], chat_id=event["chat_id"], query=event["text"], status="processing")
        db.add(record)
        db.commit()
    else:
        record = existing
        record.status = "processing"
        db.commit()
    try:
        if not record.reply:
            result = respond(db, event["chat_id"], event["text"])
            record.reply = result.text
            db.commit()  # durable answer BEFORE network send: prevent repeating /buy on retry
        ZaloClient().send_message(event["chat_id"], record.reply)
        record.status = "sent"
        record.error = ""
        db.commit()
    except Exception as exc:
        db.rollback()
        record = db.scalar(select(ChatEvent).where(ChatEvent.provider_event_id == event["event_id"]))
        if record:
            record.status = "failed"
            record.error = type(exc).__name__[:80]
            db.commit()
        log.exception("Zalo delivery failed: event=%s", event["event_id"][:12])
        raise HTTPException(status_code=503, detail="Temporary delivery failure") from exc
    return {"ok": True}

@app.post("/admin/chat", dependencies=[Depends(admin_auth)])
def admin_chat(body: TestChat, db: Session = Depends(get_session)):
    r = respond(db, body.chat_id, body.message)
    return {"text": r.text, "intent": r.intent, "evidence": r.evidence}

@app.get("/admin/products", dependencies=[Depends(admin_auth)])
def admin_products(db: Session = Depends(get_session)):
    return [{"sku": p.sku, "name": p.name, "price_vnd": p.price_vnd, "stock": p.stock, "source_url": p.source_url} for p in db.scalars(select(Product).order_by(Product.sku)).all()]

@app.get("/admin/knowledge", dependencies=[Depends(admin_auth)])
def admin_knowledge(db: Session = Depends(get_session)):
    return [{"source_key": d.source_key, "title": d.title, "approved": d.approved, "source_url": d.source_url} for d in db.scalars(select(Knowledge)).all()]

@app.post("/admin/knowledge/approve", dependencies=[Depends(admin_auth)])
def approve_knowledge(body: ApprovePayload, db: Session = Depends(get_session)):
    row = db.scalar(select(Knowledge).where(Knowledge.source_key == body.source_key))
    if not row:
        raise HTTPException(404, "Unknown source key")
    row.approved = True
    db.commit()
    return {"approved": True, "source_key": body.source_key}

@app.get("/admin/stats", dependencies=[Depends(admin_auth)])
def stats(db: Session = Depends(get_session)):
    return {
        "products": db.scalar(select(func.count()).select_from(Product)),
        "knowledge": db.scalar(select(func.count()).select_from(Knowledge)),
        "orders": db.scalar(select(func.count()).select_from(Order)),
        "chat_events": db.scalar(select(func.count()).select_from(ChatEvent)),
    }
