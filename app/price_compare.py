"""Retail price comparison based on verified SAME model/variant and source URLs.

Never fills authoritative Product prices, never invents offers or bypasses robots/403.
One named store product URL per listing; user first confirms permission to crawl.
"""
from __future__ import annotations

import csv
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import CompareModel, RetailerListing, RetailerPriceSnapshot
from app.crawler import fetch_authorized
from app.retrieval import normalize
from app.commerce import AgentResult, money
from app.config import settings

logger = logging.getLogger(__name__)
RETAILERS = {
    "tgdd": ("Thế Giới Di Động", {"www.thegioididong.com", "thegioididong.com"}),
    "cellphones": ("CellphoneS", {"cellphones.com.vn", "www.cellphones.com.vn"}),
    "hoangha": ("Hoàng Hà Mobile", {"hoanghamobile.com", "www.hoanghamobile.com"}),
    "fptshop": ("FPT Shop", {"fptshop.com.vn", "www.fptshop.com.vn"}),
    "viettelstore": ("Viettel Store", {"viettelstore.vn", "www.viettelstore.vn"}),
}
CONDITION_USED = re.compile(r"\b(?:cu|99|95|likenew|refurbished|renewed|active|kich hoat|tray xuoc|xuoc can|troi bao hanh)\b")
MODEL_HINTS = ("galaxy", "iphone", "xiaomi", "redmi", "oppo", "vivo", "realme", "poco")


def canonical_url(url: str, retailer: str) -> str:
    if retailer not in RETAILERS:
        raise ValueError(f"Nhà bán không được hỗ trợ: {retailer}")
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.hostname not in RETAILERS[retailer][1] or not parsed.path or parsed.port not in (None, 443) or parsed.username or parsed.password:
        raise ValueError("URL phải là HTTPS của đúng nhà bán, không có credentials/port tùy ý")
    # Query fragments can switch color, promotions and variant; refuse them rather than compare wrong options.
    if parsed.query or parsed.fragment:
        raise ValueError("Hãy dùng URL sản phẩm chuẩn, không có query hoặc fragment")
    return url.strip()


def compact(text: str) -> str:
    t = unicodedata.normalize("NFD", text.lower())
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn").replace("đ", "d")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", t)).strip()


def _variant_set(text: str) -> set[tuple[int,int]]:
    t = compact(text)
    results = set()
    for ram, rom in re.findall(r"\b(\d{1,2})\s*gb\s*(?:ram)?\s*(\d{2,4})\s*gb\b", t):
        results.add((int(ram), int(rom)))
    for ram, rom in re.findall(r"\b(\d{1,2})\s*(?:gb)?\s*(?:\/|\+|\-)\s*(\d{2,4})\s*(?:gb)?\b", text.lower()):
        results.add((int(ram), int(rom)))
    return results


def variant_match(model: CompareModel, product_name: str) -> tuple[bool, str]:
    name = compact(product_name)
    if not name:
        return False, "Thiếu tên sản phẩm"
    if compact(model.brand) not in name:
        return False, "Khác hãng"
    # Distinct model token (A56 != A560, A56 != A55).
    if not re.search(r"(?<![a-z0-9])" + re.escape(compact(model.model_token)) + r"(?![a-z0-9])", name):
        return False, "Sai model"
    versions = _variant_set(product_name)
    requested = (model.ram_gb, model.storage_gb)
    if requested == (0, 0):
        # Accessories require a distinctive manufacturer part number as model_token.
        # A generic marketing name (e.g. "AirPods") is NOT sufficiently specific.
        if len(compact(model.model_token).replace(" ", "")) < 5:
            return False, "Phụ kiện cần mã model/MPN đủ cụ thể"
    elif not versions or versions != {requested}:
        return False, f"Chưa xác minh RAM/ROM chính xác {requested[0]}/{requested[1]}"
    if model.condition == "new" and CONDITION_USED.search(name):
        return False, "Máy cũ/đã kích hoạt không thể so với máy mới"
    if model.condition != "new":
        return False, "Bản so sánh tự động hiện chỉ cho hàng mới"
    return True, "ok"


def _product_nodes(obj):
    if isinstance(obj, list):
        for item in obj:
            yield from _product_nodes(item)
    if isinstance(obj, dict):
        typ = obj.get("@type", [])
        if isinstance(typ, str): typ = [typ]
        if "Product" in typ:
            yield obj
        if "@graph" in obj:
            yield from _product_nodes(obj["@graph"])


def _price_number(v) -> int | None:
    """Parse integer VND or formatted thousands; avoid decimal-concatenation errors."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        candidate = v
    elif isinstance(v, float):
        candidate = int(v) if v.is_integer() else 0
    elif isinstance(v, str):
        raw = re.sub(r"[₫đ\s]", "", v.strip())
        # Merchant structured data sometimes uses decimal .00 / ,00.
        if re.fullmatch(r"\d{4,}(?:[.,]00)", raw):
            raw = raw[:-3]
        elif re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+(?:[.,]00)", raw):
            raw = raw[:-3]
        if re.fullmatch(r"\d+", raw):
            candidate = int(raw)
        elif re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw):
            candidate = int(re.sub(r"[.,]", "", raw))
        else:
            return None
    else:
        return None
    return candidate if 10_000 <= candidate <= 200_000_000 else None


def parse_retail_html(model: CompareModel, url: str, html: str) -> tuple[str, int, str]:
    """Fail closed if model, RAM/ROM, condition or price provenance is ambiguous."""
    soup = BeautifulSoup(html[:1_000_000], "html.parser")
    # Prefer selected product H1. Multi-variant pages with generic H1 must be manually reviewed.
    h1 = soup.find("h1")
    h1_text = h1.get_text(" ", strip=True) if h1 else ""
    candidates: list[tuple[str, int, str]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            objects = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        for obj in _product_nodes(objects):
            product_name = str(obj.get("name") or "")[:250]
            matching, _ = variant_match(model, product_name)
            if not matching:
                continue
            offers = obj.get("offers", [])
            if isinstance(offers, dict): offers = [offers]
            for offer in offers if isinstance(offers, list) else []:
                if not isinstance(offer, dict): continue
                currency = str(offer.get("priceCurrency", "VND")).upper()
                if currency != "VND": continue
                p = _price_number(offer.get("price"))
                if p is not None:
                    candidates.append((product_name, p, "jsonld"))
    if candidates:
        if len({p for _, p, _ in candidates}) != 1:
            raise ValueError("Nhiều giá JSON-LD không nhất quán; phải xác minh thủ công")
        return candidates[0]
    # Preserve currency: a numeric amount with USD/EUR must never be treated as VND.
    currency_tag = soup.select_one('meta[property="product:price:currency"],meta[property="og:price:currency"]')
    if currency_tag and str(currency_tag.get("content", "")).upper() != "VND":
        raise ValueError("Đơn vị tiền tệ không phải VND")
    accepted, reason = variant_match(model, h1_text)
    if not accepted:
        raise ValueError("Từ chối ghép sản phẩm: " + reason)
    selectors = [
        'meta[property="product:price:amount"]',
        'meta[property="og:price:amount"]',
        'meta[itemprop="price"]',
        '[itemprop="offers"] [itemprop="price"]',
    ]
    for selector in selectors:
        matches = soup.select(selector)
        values = {v for item in matches if (v := _price_number(item.get("content") or item.get("value"))) is not None}
        if len(values) > 1:
            raise ValueError("Metadata có giá khác nhau, cần duyệt")
        if len(values) == 1:
            return h1_text[:250], values.pop(), "meta"
    raise ValueError("Không tìm thấy giá được cấu trúc rõ ràng; không lấy giá mơ hồ từ toàn trang")


def import_watchlist(db: Session, path: str | Path) -> tuple[int, int]:
    added = 0
    skipped = 0
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            key = row["model_key"].strip().upper()
            store = row["retailer"].strip().lower()
            uri = row["url"].strip()
            if not uri:
                skipped += 1
                continue
            uri = canonical_url(uri, store)
            ram, storage = int(row["ram_gb"]), int(row["storage_gb"])
            if not ((ram == 0 and storage == 0) or (1 <= ram <= 64 and 16 <= storage <= 4096)):
                raise ValueError("Need explicit RAM/ROM or 0/0 + exact manufacturer part number")
            expected = {
                "name": row["name"].strip()[:200], "brand": row["brand"].strip()[:90],
                "model_token": row["model_token"].strip()[:80], "ram_gb": ram,
                "storage_gb": storage, "condition": row["condition"].strip().lower(),
            }
            if expected["condition"] != "new":
                raise ValueError("Only NEW condition supported in automated comparison")
            model = db.scalar(select(CompareModel).where(CompareModel.model_key == key))
            if model is None:
                model = CompareModel(model_key=key, **expected)
                db.add(model)
                db.flush()
            elif any(getattr(model, col) != val for col, val in expected.items()):
                raise ValueError("Model key conflict: " + key)
            listing = db.scalar(select(RetailerListing).where(RetailerListing.url == uri))
            if listing is not None:
                if listing.model_id != model.id or listing.retailer != store:
                    raise ValueError("URL already linked to different product/retailer")
                # Re-importing same CSV may enable/disable crawl only through a deliberate edit.
                listing.enabled = row.get("crawl_enabled", "false").strip().lower() == "true"
                skipped += 1
                continue
            db.add(RetailerListing(model_id=model.id, retailer=store, url=uri,
                                   enabled=row.get("crawl_enabled", "false").strip().lower() == "true"))
            added += 1
        db.commit()
    return added, skipped


def _persist_snapshot(db: Session, listing: RetailerListing, name: str, value: int, method: str, at: datetime | None = None):
    model = db.get(CompareModel, listing.model_id)
    match, reason = variant_match(model, name)
    if not match:
        raise ValueError("Tên sản phẩm không khớp: " + reason)
    if _price_number(value) is None:
        raise ValueError("Giá không hợp lệ")
    db.add(RetailerPriceSnapshot(listing_id=listing.id, price_vnd=value,
                                 page_product_name=name[:250], method=method,
                                 observed_at=at or datetime.now(timezone.utc)))
    listing.last_status = "verified"
    listing.last_error = ""
    listing.checked_at = at or datetime.now(timezone.utc)
    db.commit()


def import_reviewed_prices(db: Session, path: str | Path) -> int:
    """Offline fallback for sources disallowing automated access; operator must verify URL and price."""
    total = 0
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            if row.get("verified_by_human", "").strip().lower() != "yes":
                raise ValueError("verified_by_human=yes required, review price and product page first")
            uri = row["url"].strip()
            listing = db.scalar(select(RetailerListing).where(RetailerListing.url == uri))
            if not listing:
                raise ValueError("URL not registered in watchlist: " + uri)
            p = _price_number(row["price_vnd"].strip())
            if p is None:
                raise ValueError("Invalid price")
            _persist_snapshot(db, listing, row["page_product_name"].strip(), p, "manual_review")
            total += 1
    return total


def crawl_watchlist(db: Session, *, permission_confirmed: bool = False, model_key: str | None = None) -> dict:
    if not permission_confirmed:
        raise ValueError("Requires explicit --ack-permission and source permission/terms review")
    q = select(RetailerListing).where(RetailerListing.enabled.is_(True))
    if model_key:
        m = db.scalar(select(CompareModel).where(CompareModel.model_key == model_key.upper()))
        if m is None:
            return {"fetched": 0, "failed": 0, "skipped": 0, "error": "Unknown model"}
        q = q.where(RetailerListing.model_id == m.id)
    listings = db.scalars(q.order_by(RetailerListing.id)).all()
    result = {"fetched": 0, "failed": 0, "skipped": 0, "details": []}
    for listing in listings[:30]:
        try:
            # Site-level explicit allowlist + DNS check + robots fail-closed, no redirect, no anti-bot bypass.
            html = fetch_authorized(listing.url, settings.allowed_hosts)
            model = db.get(CompareModel, listing.model_id)
            name, price, method = parse_retail_html(model, listing.url, html)
            _persist_snapshot(db, listing, name, price, method)
            result["fetched"] += 1
            result["details"].append(f"{listing.retailer}: verified {price}")
        except Exception as exc:
            db.rollback()
            listing.last_status = "failed"
            listing.last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
            listing.checked_at = datetime.now(timezone.utc)
            db.commit()
            result["failed"] += 1
            result["details"].append(f"{listing.retailer}: {listing.last_error}")
        # Rate limit always, even failed requests.
        import time
        time.sleep(3)
    return result


def _latest(db: Session, listing_id: int) -> RetailerPriceSnapshot | None:
    return db.scalar(select(RetailerPriceSnapshot).where(
        RetailerPriceSnapshot.listing_id == listing_id).order_by(
        RetailerPriceSnapshot.observed_at.desc(), RetailerPriceSnapshot.id.desc()).limit(1))


def compare(db: Session, query: str) -> AgentResult:
    raw = query.strip()
    if raw.lower().startswith("/compare"):
        raw = raw[len("/compare"):].strip()
    models = db.scalars(select(CompareModel).order_by(CompareModel.model_key)).all()
    if not raw or compact(raw) in {"gia", "so sanh", "so sanh gia"}:
        keys = [f"• {m.model_key}: {m.name}" for m in models[:10]]
        return AgentResult("Dùng /compare MÃ_SẢN_PHẨM. Danh mục đang theo dõi:\n" + ("\n".join(keys) if keys else "Chưa có danh mục"), "compare_catalog", [])
    query_text = compact(raw)
    model = next((m for m in models if m.model_key.lower() == raw.lower()), None)
    if model is None:
        # For natural-language search, require at least brand, model and memory variant;
        # never guess among 8/128 and 8/256.
        for m in models:
            if compact(m.brand) in query_text and re.search(r"\b" + re.escape(compact(m.model_token)) + r"\b", query_text) and ((m.ram_gb == 0 and m.storage_gb == 0) or (m.ram_gb, m.storage_gb) in _variant_set(raw)):
                model = m
                break
    if model is None:
        return AgentResult("Chưa xác định được đúng phiên bản. Gõ /compare để xem mã đã theo dõi; cần đúng RAM/ROM (ví dụ 8GB/128GB).", "compare_unknown", [])
    listings = db.scalars(select(RetailerListing).where(RetailerListing.model_id == model.id).order_by(RetailerListing.retailer)).all()
    if not listings:
        return AgentResult(f"{model.name}: chưa có link sản phẩm để theo dõi.", "compare_empty", [model.model_key])
    fresh = []
    stale = []
    unknown = []
    now = datetime.now(timezone.utc)
    for item in listings:
        p = _latest(db, item.id)
        if p is None:
            unknown.append(item)
        else:
            ts = p.observed_at if p.observed_at.tzinfo else p.observed_at.replace(tzinfo=timezone.utc)
            entry = (item, p)
            (fresh if now - ts <= timedelta(hours=24) else stale).append(entry)
    fresh.sort(key=lambda r: (r[1].price_vnd, r[0].retailer))
    stale.sort(key=lambda r: (r[1].price_vnd, r[0].retailer))
    variant_label = f"{model.ram_gb}/{model.storage_gb}" if model.ram_gb and model.storage_gb else f"MPN {model.model_token}"
    text = f"📊 So sánh {model.name} (hàng mới, đúng {variant_label})\n"
    if fresh:
        a, p = fresh[0]
        text += f"✅ Giá thấp nhất TRONG CÁC GIÁ ĐÃ XÁC MINH (24h): {RETAILERS[a.retailer][0]} — {money(p.price_vnd)}\n"
        if len(fresh) >= 2:
            diff = fresh[-1][1].price_vnd - p.price_vnd
            text += f"Chênh lệch cao nhất: {money(diff)}.\n"
    elif stale:
        text += "⚠️ Chưa có giá kiểm tra trong 24h, chỉ hiển thị dữ liệu cũ.\n"
    else:
        text += "⚠️ Chưa có giá xác minh. Hãy mở link và kiểm tra thủ công.\n"
    for item, price in fresh[:4]:
        at = (price.observed_at.replace(tzinfo=timezone.utc) if price.observed_at.tzinfo is None else price.observed_at).astimezone(timezone(timedelta(hours=7))).strftime("%d/%m %H:%M") + " VN"
        text += f"\n• {RETAILERS[item.retailer][0]}: {money(price.price_vnd)} ({at}; {price.method})\n{item.url}\n"
    for item, price in stale[:3]:
        at = (price.observed_at.replace(tzinfo=timezone.utc) if price.observed_at.tzinfo is None else price.observed_at).astimezone(timezone(timedelta(hours=7))).strftime("%d/%m %H:%M") + " VN"
        text += f"\n• {RETAILERS[item.retailer][0]}: {money(price.price_vnd)} [CŨ: {at}]\n{item.url}\n"
    for item in unknown[:4]:
        text += f"\n• {RETAILERS[item.retailer][0]}: chưa có giá xác minh\n{item.url}\n"
    text += "\nGiá niêm yết chưa gồm các điều kiện riêng (voucher, thành viên, vận chuyển, bảo hành). Giá thấp nhất chưa chắc mua có lợi nhất; mở link để xác nhận."
    return AgentResult(text[:1940], "price_comparison", [item.url for item in listings])
