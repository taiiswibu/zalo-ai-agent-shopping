"""Local development only: getUpdates is mutually exclusive with webhook."""
from __future__ import annotations
import logging
import time
from sqlalchemy import select
from app.config import settings
from app.db import init_db, SessionLocal
from app.models import ChatEvent
from app.agent import respond
from app.zalo import ZaloClient, parse_update

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def handle(update: dict, client: ZaloClient) -> None:
    event = parse_update(update)
    if not event:
        return
    with SessionLocal() as db:
        old = db.scalar(select(ChatEvent).where(ChatEvent.provider_event_id == event["event_id"]))
        if old and old.status in {"sent", "processing"}:
            return
        if old is None:
            old = ChatEvent(provider_event_id=event["event_id"], chat_id=event["chat_id"], query=event["text"], status="processing")
            db.add(old)
            db.commit()
        try:
            if not old.reply:
                result = respond(db, event["chat_id"], event["text"])
                old.reply = result.text
                db.commit()
            client.send_message(event["chat_id"], old.reply)
            old.status = "sent"
            db.commit()
            log.info("sent response to event=%s", event["event_id"][:12])
        except Exception:
            db.rollback()
            old = db.scalar(select(ChatEvent).where(ChatEvent.provider_event_id == event["event_id"]))
            if old:
                old.status = "failed"
                db.commit()
            raise


def main():
    if not settings.bot_token or settings.bot_mode != "live":
        raise SystemExit("Set BOT_TOKEN and BOT_MODE=live in .env")
    init_db()
    client = ZaloClient()
    print("Polling local: nếu bot đã có webhook, hãy xóa webhook trước bằng lệnh scripts/zalo_setup.py delete")
    while True:
        try:
            payload = client.call("getUpdates", {"timeout": "25"}, timeout=35)
            result = payload.get("result", [])
            events = result if isinstance(result, list) else [result]
            for e in events:
                if not isinstance(e, dict):
                    continue
                # getUpdates examples reference same envelope as webhook.
                event = e if "result" in e else {"ok": True, "result": e}
                handle(event, client)
        except KeyboardInterrupt:
            break
        except Exception:
            log.exception("Polling failed; retrying")
            time.sleep(5)

if __name__ == "__main__":
    main()
