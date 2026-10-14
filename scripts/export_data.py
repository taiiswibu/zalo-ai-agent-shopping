"""Portable CSV snapshots for local/cloud migrations. Keep exports private (chat IDs)."""
from __future__ import annotations
import csv
import os
from pathlib import Path
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Product, Knowledge, Order

out = Path(os.environ.get("EXPORT_DIR", "data/exports"))
out.mkdir(parents=True, exist_ok=True)
for model, cols in [
    (Product, ["sku", "name", "category", "description", "price_vnd", "stock", "warranty_months", "source_url", "is_authoritative"]),
    (Knowledge, ["source_key", "title", "content", "source_url", "approved"]),
    (Order, ["code", "chat_id", "sku", "quantity", "unit_price_vnd", "total_vnd", "status"]),
]:
    with SessionLocal() as db, (out / f"{model.__tablename__}.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for row in db.scalars(select(model)).all():
            w.writerow([getattr(row, c) for c in cols])
print("Exported to", out)
