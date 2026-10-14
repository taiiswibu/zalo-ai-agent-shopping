"""Small explicitly allowlisted, robots-aware HTML collector; never bypass access controls."""
from __future__ import annotations
import ipaddress
import json
import re
import socket
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Knowledge, Product, CrawlRun, CrawlObservation
from app.config import settings

USER_AGENT = "ZaloAgentPortfolioCrawler/1.0 (+respect-robots)"


def validate_target(url: str, allowed_hosts: set[str]) -> str:
    p = urlparse(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ValueError("Chỉ hỗ trợ URL HTTPS công khai, không có credentials/port tùy ý")
    host = p.hostname.lower().rstrip(".")
    if host not in allowed_hosts:
        raise ValueError("Host chưa nằm trong CRAWL_ALLOWED_HOSTS")
    # Block redirect-based host changes and private/local destinations.
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("Không phân giải được DNS")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("Không crawl địa chỉ IP private/reserved")
    return host


def fetch_authorized(url: str, allowed_hosts: set[str]) -> str:
    host = validate_target(url, allowed_hosts)
    # Fail closed if robots.txt cannot be checked.
    robots_url = f"https://{host}/robots.txt"
    with httpx.Client(timeout=10, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client:
        robots = client.get(robots_url)
        if robots.status_code != 200:
            raise ValueError("Không xác minh được robots.txt; bỏ qua nguồn")
        rp = RobotFileParser()
        rp.parse(robots.text.splitlines())
        if not rp.can_fetch(USER_AGENT, url):
            raise ValueError("robots.txt từ chối crawler")
        res = client.get(url)
        if res.status_code in {301, 302, 303, 307, 308}:
            raise ValueError("Không tự động theo redirect")
        res.raise_for_status()
        if "text/html" not in res.headers.get("content-type", ""):
            raise ValueError("Chỉ cho phép text/html")
        if len(res.content) > 1_000_000:
            raise ValueError("HTML vượt quá 1MB")
        return res.text


def extract_html(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.select("script:not([type='application/ld+json']), style, noscript, nav, footer"):
        tag.decompose()
    title = (soup.title.get_text(" ", strip=True) if soup.title else url)[:240]
    meta = soup.select_one('meta[name="description"]')
    desc = meta.get("content", "") if meta else ""
    data = {"title": title, "url": url, "description": desc, "product": None}
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            obj = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        items = obj if isinstance(obj, list) else [obj]
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("@graph"), list):
                items += item["@graph"]
            if not isinstance(item, dict):
                continue
            types = item.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if "Product" not in types:
                continue
            offer = item.get("offers", {})
            if isinstance(offer, list):
                offer = offer[0] if offer else {}
            data["product"] = {
                "name": str(item.get("name") or title)[:240],
                "sku": str(item.get("sku") or "")[:80],
                "description": str(item.get("description") or desc)[:3000],
                "observed_price": offer.get("price") if isinstance(offer, dict) else None,
                "price_currency": offer.get("priceCurrency") if isinstance(offer, dict) else None,
            }
            break
        if data["product"]:
            break
    paragraphs = [p.get_text(" ", strip=True) for p in soup.select("main p, article p, h1, h2")]
    if not paragraphs:
        paragraphs = [soup.get_text(" ", strip=True)]
    data["text"] = "\n".join(p[:600] for p in paragraphs if len(p) >= 20)[:12000]
    return data


def ingest_page(db: Session, url: str, html: str) -> dict:
    page = extract_html(url, html)
    product = page["product"]
    run = CrawlRun(url=url, status="processed")
    db.add(run)
    digest = __import__("hashlib").sha256(html.encode("utf-8")).hexdigest()
    if product is not None and not db.scalar(select(CrawlObservation).where(
            CrawlObservation.source_url == url, CrawlObservation.source_hash == digest)):
        db.add(CrawlObservation(source_url=url, source_hash=digest, payload=product))
    # A crawled page is an unapproved knowledge candidate, never a trusted price/stock source.
    key = "crawl:" + __import__("hashlib").sha256(url.encode()).hexdigest()[:24]
    content = (page["title"] + "\n" + (product["description"] if product else page["description"]) + "\n" + page["text"])[:14000]
    existing = db.scalar(select(Knowledge).where(Knowledge.source_key == key))
    if existing:
        if existing.content != content:
            existing.approved = False  # re-review changed content
        existing.content = content
        existing.title = page["title"]
        existing.source_url = url
    else:
        db.add(Knowledge(source_key=key, title=page["title"], content=content, source_url=url, approved=False))
    db.commit()
    return {"source_key": key, "product_detected": bool(product), "approved": False, "product_observation": product}
