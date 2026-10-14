from __future__ import annotations
import json
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Product, Knowledge


def load_demo(db: Session, path: str | Path = "data/demo_catalog.json") -> tuple[int, int]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    np = nk = 0
    for row in payload["products"]:
        p = db.scalar(select(Product).where(Product.sku == row["sku"]))
        if p is None:
            db.add(Product(**row, is_authoritative=True, source_url="demo://catalog"))
            np += 1
    for policy in payload["policies"]:
        if not db.scalar(select(Knowledge).where(Knowledge.source_key == policy["key"])):
            db.add(Knowledge(source_key=policy["key"], title=policy["title"], content=policy["content"], source_url="demo://policy/" + policy["key"], approved=True))
            nk += 1
    db.commit()
    return np, nk
