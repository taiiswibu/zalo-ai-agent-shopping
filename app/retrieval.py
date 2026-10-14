from __future__ import annotations
import math
import re
import unicodedata
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Product, Knowledge


def normalize(text: str) -> str:
    s = unicodedata.normalize("NFD", text.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d")
    return re.sub(r"[^a-z0-9 ]", " ", s)

STOP = set("toi ban minh co can mot cho cua va la khong thi the nao bao nhieu gia san pham tim mua nhe shop con duoc".split())

def terms(text: str) -> set[str]:
    return {w for w in normalize(text).split() if len(w) > 1 and w not in STOP}

def score(query: str, text: str) -> float:
    a, b = terms(query), terms(text)
    if not a or not b:
        return 0.0
    common = a & b
    return len(common) / math.sqrt(len(a) * len(b)) if common else 0.0

def find_products(db: Session, query: str, *, max_price: int | None = None, in_stock_only: bool = False, limit: int = 5) -> list[Product]:
    all_products = db.scalars(select(Product).order_by(Product.sku)).all()
    products = [p for p in all_products if (max_price is None or p.price_vnd <= max_price) and (not in_stock_only or p.stock > 0)]
    if not query.strip():
        return products[:limit]
    ranked = sorted(((score(query, p.name + " " + p.category + " " + p.description + " " + p.sku), p) for p in products), key=lambda x: (-x[0], x[1].sku))
    return [p for s,p in ranked if s > 0][:limit]

def find_knowledge(db: Session, query: str, limit: int = 2) -> list[Knowledge]:
    docs = db.scalars(select(Knowledge).where(Knowledge.approved.is_(True))).all()
    ranked = sorted(((score(query, d.title + " " + d.content), d) for d in docs), key=lambda x: -x[0])
    return [d for s,d in ranked if s >= 0.12][:limit]
