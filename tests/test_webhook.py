from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.db import create_db_engine, get_session
from app.models import Base, ChatEvent
from app.api import app
from app.config import settings
from app.seed import load_demo
from pathlib import Path


def test_webhook_auth_idempotency(tmp_path, monkeypatch):
    engine = create_db_engine("sqlite:///" + str(tmp_path / "webhook.db"))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        load_demo(db, Path(__file__).parent.parent / "data/demo_catalog.json")
    def override():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_session] = override
    monkeypatch.setattr("app.api.verify_secret", lambda value: value == "test-secret")
    monkeypatch.setattr("app.api.ZaloClient", lambda: type("Client", (), {"send_message": lambda *args: {"ok": True}})())
    msg = {"ok": True, "result": {"event_name": "message.text.received", "message": {
        "chat": {"id": "user-1", "chat_type": "PRIVATE"}, "message_id": "test-123", "text": "/products"}}}
    try:
        with TestClient(app) as client:
            assert client.post("/webhooks/zalo", json=msg).status_code == 403
            r = client.post("/webhooks/zalo", json=msg, headers={"X-Bot-Api-Secret-Token": "test-secret"})
            assert r.status_code == 200
            duplicate = client.post("/webhooks/zalo", json=msg, headers={"X-Bot-Api-Secret-Token": "test-secret"})
            assert duplicate.json().get("duplicate") is True
        with Session(engine) as db:
            assert db.query(ChatEvent).count() == 1
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_retry_does_not_duplicate_order(tmp_path, monkeypatch):
    from sqlalchemy import select
    from app.models import Order
    engine = create_db_engine("sqlite:///" + str(tmp_path / "retry.db"))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        load_demo(db, Path(__file__).parent.parent / "data/demo_catalog.json")
    def override():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_session] = override
    monkeypatch.setattr("app.api.verify_secret", lambda value: value == "test-secret")
    attempts = {"n": 0}
    def sender(*args):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("Network unavailable")
        return {"ok": True}
    monkeypatch.setattr("app.api.ZaloClient", lambda: type("Client", (), {"send_message": sender})())
    msg = {"ok": True, "result": {"event_name": "message.text.received", "message": {
        "chat": {"id": "buyer-a", "chat_type": "PRIVATE"}, "message_id": "order-event-1", "text": "/buy TAI-001 1"}}}
    try:
        with TestClient(app) as client:
            one = client.post("/webhooks/zalo", json=msg, headers={"X-Bot-Api-Secret-Token": "test-secret"})
            two = client.post("/webhooks/zalo", json=msg, headers={"X-Bot-Api-Secret-Token": "test-secret"})
            assert one.status_code == 503 and two.status_code == 200
        with Session(engine) as db:
            assert len(db.scalars(select(Order)).all()) == 1
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
