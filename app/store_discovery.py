"""Conservative category-page discovery; exports human-review CSV, does not auto-publish prices.

Fetch only HTTPS and allowlisted sites through crawler.fetch_authorized, respecting robots.txt.
Never follows redirects, bypasses blocks, or probes undocumented APIs.
"""
from __future__ import annotations

import csv
from pathlib import Path
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.crawler import fetch_authorized
from app.config import settings
from app.models import CompareModel
from app.price_compare import RETAILERS, canonical_url, variant_match


def discover_links(html: str, category_url: str, retailer: str, *, limit: int = 80) -> list[dict]:
    if retailer not in RETAILERS:
        raise ValueError("Unsupported retailer")
    if not 1 <= limit <= 100:
        raise ValueError("Limit 1..100")
    # Verify source URL belongs to the specified retailer before trusting anchors.
    canonical_url(category_url, retailer)
    soup = BeautifulSoup(html[:1_000_000], "html.parser")
    items, seen = [], set()
    for a in soup.select("a[href]"):
        u = urljoin(category_url, str(a.get("href") or ""))
        p = urlparse(u)
        clean = p._replace(query="", fragment="").geturl()
        try:
            canonical_url(clean, retailer)
        except ValueError:
            continue
        path = p.path.lower()
        # Only candidate product-detail paths; never searches, carts or user pages.
        if retailer == "tgdd":
            ok = path.startswith("/tai-nghe/") and len(path.split("/")) >= 3
        elif retailer == "cellphones":
            ok = path.endswith(".html") and ("tai-nghe" in path or "headphone" in path or "buds" in path)
        elif retailer == "fptshop":
            ok = path.startswith("/phu-kien/") and len(path.split("/")) >= 3 and "tai-nghe" in path
        else:
            ok = ("tai-nghe" in path or "headphone" in path or "earphone" in path) and len(path.split("/")) >= 3
        if not ok or clean in seen:
            continue
        seen.add(clean)
        label = a.get_text(" ", strip=True)[:240]
        if len(label) < 6:
            label = str(a.get("title") or "")[:240]
        items.append({"retailer": retailer, "url": clean, "label": label, "match_status": "needs_review"})
        if len(items) >= limit:
            break
    return items


def review_matches(db: Session, items: list[dict]) -> list[dict]:
    models = db.scalars(select(CompareModel)).all()
    reviewed=[]
    for item in items:
        exact = [m for m in models if variant_match(m, item["label"])[0]]
        guess = exact[0] if len(exact) == 1 else None
        reviewed.append({**item, "model_key_guess": guess.model_key if guess else "",
                         "match_status": "candidate_review_required" if guess else "unmatched"})
    return reviewed


def discover_category(db: Session, retailer: str, url: str, destination: str, *, limit: int = 80, permission_confirmed: bool = False) -> dict:
    if not permission_confirmed:
        raise ValueError("Explicit source permission confirmation required")
    canonical_url(url, retailer)
    html = fetch_authorized(url, settings.allowed_hosts)
    rows = review_matches(db, discover_links(html, url, retailer, limit=limit))
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        fields = ["retailer", "url", "label", "model_key_guess", "match_status"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return {"found": len(rows), "matched_candidates": sum(bool(r["model_key_guess"]) for r in rows), "review_csv": str(path)}
