"""One scheduled batch; no infinite daemon. Respect robots.txt, allowlist and delay."""
from __future__ import annotations
import argparse
import time
from pathlib import Path
from app.config import settings
from app.db import init_db, SessionLocal
from app.crawler import fetch_authorized, ingest_page
from app.models import CrawlRun

parser = argparse.ArgumentParser()
parser.add_argument("--sources", default="data/crawl_sources.txt")
parser.add_argument("--delay", type=float, default=3.0)
args = parser.parse_args()
if args.delay < 2:
    raise SystemExit("delay phải từ 2 giây trở lên")
init_db()
urls = [line.strip() for line in Path(args.sources).read_text(encoding="utf-8").splitlines() if line.strip() and not line.strip().startswith("#")]
if not settings.allowed_hosts and urls:
    raise SystemExit("CRAWL_ALLOWED_HOSTS trống")
for url in urls[:50]:
    try:
        html = fetch_authorized(url, settings.allowed_hosts)
        with SessionLocal() as db:
            print(url, ingest_page(db, url, html))
    except Exception as e:
        with SessionLocal() as db:
            db.add(CrawlRun(url=url, status="failed", note=str(e)[:600]))
            db.commit()
        print("SKIPPED", url, type(e).__name__, str(e)[:200])
    time.sleep(args.delay)
