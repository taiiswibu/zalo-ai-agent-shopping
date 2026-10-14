"""Offline, deterministic smoke evaluation; never sends prompts to an external model."""
from __future__ import annotations
from sqlalchemy.orm import Session
from app.agent import respond
from app.models import Product
from sqlalchemy import select

CASES = [
    ("/start", "help", "bảo hành"),
    ("/products", "catalog", "TAI-001"),
    ("tai nghe dưới 1 triệu", "product_search", "TAI-001"),
    ("bảo hành thế nào?", "policy", "Nguồn"),
    ("giao hàng thế nào", "policy", "Nguồn"),
    ("đổi trả", "policy", "Nguồn"),
    ("/orders", "orders", "đơn"),
    ("sạc nhanh", "product_search", "SAC-001"),
]


def run_eval(db: Session) -> dict:
    scores = []
    for idx, (question, intent, expected) in enumerate(CASES):
        result = respond(db, f"eval-{idx}", question)
        passed = result.intent == intent and expected.casefold() in result.text.casefold()
        scores.append({"question": question, "passed": passed, "intent": result.intent})
    return {"passed": sum(x["passed"] for x in scores), "total": len(scores), "cases": scores}
