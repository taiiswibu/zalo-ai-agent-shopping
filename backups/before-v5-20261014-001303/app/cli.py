from __future__ import annotations
import argparse
import csv
from sqlalchemy import select
from app.db import init_db, SessionLocal
from app.models import Product
from app.seed import load_demo
from app.crawler import fetch_authorized, ingest_page


def import_catalog(path: str) -> int:
    count = 0
    with SessionLocal() as db, open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        needed = {"sku", "name", "price_vnd", "stock", "warranty_months", "description"}
        if not needed.issubset(reader.fieldnames or []):
            raise ValueError("CSV cần có cột: " + ", ".join(sorted(needed)))
        for row in reader:
            sku = row["sku"].strip().upper()
            if not sku or not row["name"].strip():
                continue
            values = {"name": row["name"].strip(), "price_vnd": int(row["price_vnd"]),
                      "stock": int(row["stock"]), "warranty_months": int(row["warranty_months"]),
                      "description": row["description"].strip(), "category": row.get("category", "phu-kien").strip(),
                      "source_url": row.get("source_url", "owned://catalog").strip(), "is_authoritative": True}
            if min(values["price_vnd"], values["stock"], values["warranty_months"]) < 0:
                raise ValueError("Dữ liệu số không được âm")
            p = db.scalar(select(Product).where(Product.sku == sku))
            if p:
                for name, val in values.items():
                    setattr(p, name, val)
            else:
                db.add(Product(sku=sku, **values))
            count += 1
        db.commit()
    return count


def main():
    parser = argparse.ArgumentParser(description="Quản trị bot và nạp dữ liệu")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    sub.add_parser("seed-shopping", help="Nạp link sản phẩm và thông số đã đối chiếu (KHÔNG nạp giá giả)")
    specs=sub.add_parser("import-shopping-specs", help="Nạp thông số sản phẩm từ CSV có link nguồn")
    specs.add_argument("path")
    bulk=sub.add_parser("bulk-watchlist", help="Nhập nhiều trang sản phẩm (giá chưa xác minh)")
    bulk.add_argument("path")
    sub.add_parser("shopping-status", help="Thống kê độ phủ link/giá quan sát")
    discover=sub.add_parser("discover-category", help="Dò link ứng viên trên trang danh mục được phép; chỉ xuất CSV để duyệt")
    discover.add_argument("--retailer", required=True, choices=["tgdd","cellphones","fptshop","hoangha","viettelstore"])
    discover.add_argument("--url", required=True)
    discover.add_argument("--out", default="data/discovered_links_review.csv")
    discover.add_argument("--limit", type=int, default=60)
    discover.add_argument("--ack-permission", action="store_true")
    sub.add_parser("seed-demo")
    indexer = sub.add_parser("reindex")
    indexer.add_argument("--embed", action="store_true", help="Build Gemini vectors (requires RAG_BACKEND=gemini + API key)")
    sub.add_parser("eval")
    sub.add_parser("traces")
    sub.add_parser("tool-traces", help="Lịch sử công cụ Gemini đã gọi, KHÔNG in Bot Token")
    sub.add_parser("agent-status", help="Kiểm tra Gemini/ToolNode có thể khởi động không")
    sub.add_parser("observations")
    w = sub.add_parser("compare-import", help="Nhập URL nhà bán từ CSV (không tự tạo giá)")
    w.add_argument("path")
    prices = sub.add_parser("compare-reviewed", help="Nhập giá đã đối chiếu thủ công và kèm nguồn")
    prices.add_argument("path")
    query = sub.add_parser("compare", help="So sánh giá đã lưu")
    query.add_argument("query", nargs="?", default="")
    fetch = sub.add_parser("compare-crawl", help="Crawl URL cho phép và robots.txt chấp thuận")
    fetch.add_argument("--ack-permission", action="store_true", required=False)
    fetch.add_argument("--model", default="")
    catalog = sub.add_parser("import-csv")
    catalog.add_argument("path")
    fdoc = sub.add_parser("ingest-file")
    fdoc.add_argument("path")
    crawl = sub.add_parser("crawl-url")
    crawl.add_argument("url")
    args = parser.parse_args()
    init_db()
    if args.cmd == "init-db":
        print("Database created")
    elif args.cmd == "seed-shopping":
        from app.catalog_advisor import seed_headphones
        with SessionLocal() as db:
            print(seed_headphones(db))
    elif args.cmd == "import-shopping-specs":
        from app.catalog_advisor import import_specs
        with SessionLocal() as db:
            print(import_specs(db,args.path))
    elif args.cmd == "bulk-watchlist":
        from app.price_compare import import_watchlist
        with SessionLocal() as db:
            print(import_watchlist(db,args.path))
    elif args.cmd == "discover-category":
        from app.store_discovery import discover_category
        with SessionLocal() as db:
            print(discover_category(db,args.retailer,args.url,args.out,limit=args.limit,permission_confirmed=args.ack_permission))
    elif args.cmd == "shopping-status":
        from app.models import BuyerProductSpec, CompareModel, RetailerListing, RetailerPriceSnapshot
        from sqlalchemy import func
        with SessionLocal() as db:
            print({name:db.scalar(select(func.count()).select_from(table)) for name,table in [("models",CompareModel),("product_specs",BuyerProductSpec),("retailer_links",RetailerListing),("price_observations",RetailerPriceSnapshot)]})
    elif args.cmd == "seed-demo":
        with SessionLocal() as db:
            print("Seeded products, policies:", load_demo(db))
    elif args.cmd == "reindex":
        from app.rag import sync_index
        with SessionLocal() as db:
            print(sync_index(db, build_vectors=args.embed))
    elif args.cmd == "eval":
        from app.evaluate import run_eval
        with SessionLocal() as db:
            print(run_eval(db))
    elif args.cmd == "traces":
        from app.models import AgentTrace
        with SessionLocal() as db:
            for x in db.scalars(select(AgentTrace).order_by(AgentTrace.id.desc()).limit(15)):
                print(f"{x.id}: route={x.intent} engine={x.engine} provider={x.llm_provider} latency={x.latency_ms}ms evidence={x.retrieval_keys}")
    elif args.cmd == "tool-traces":
        from app.models import AgentToolTrace
        with SessionLocal() as db:
            for t in db.scalars(select(AgentToolTrace).order_by(AgentToolTrace.id.desc()).limit(20)):
                print(f"{t.id}: tool={t.tool_name} intent={t.result_intent} status={t.status} at={t.created_at}")
    elif args.cmd == "agent-status":
        from app.config import settings
        import importlib.util
        has_graph = importlib.util.find_spec("langgraph") is not None
        has_core = importlib.util.find_spec("langchain_core") is not None
        print({"provider": settings.llm_provider,
               "gemini_key_configured": bool(settings.gemini_api_key),
               "model": settings.gemini_model,
               "langgraph_installed": has_graph,
               "langchain_core_installed": has_core,
               "tool_agent_ready": settings.llm_provider == "gemini" and bool(settings.gemini_api_key) and has_graph and has_core,
               "note": "Ready không có nghĩa Gemini API đã được kiểm tra/Free Tier còn quota"})
    elif args.cmd == "observations":
        from app.models import CrawlObservation
        with SessionLocal() as db:
            for x in db.scalars(select(CrawlObservation).order_by(CrawlObservation.id.desc()).limit(30)):
                print(x.id, x.source_url, x.payload)
    elif args.cmd in {"compare-import", "compare-reviewed", "compare", "compare-crawl"}:
        from app import price_compare as pc
        with SessionLocal() as db:
            if args.cmd == "compare-import":
                print("Imported / existing:", pc.import_watchlist(db, args.path))
            elif args.cmd == "compare-reviewed":
                print("Manually reviewed prices imported:", pc.import_reviewed_prices(db, args.path))
            elif args.cmd == "compare":
                print(pc.compare(db, args.query).text)
            else:
                print(pc.crawl_watchlist(db, permission_confirmed=args.ack_permission, model_key=args.model or None))
    elif args.cmd == "import-csv":
        print("Imported rows:", import_catalog(args.path))
    elif args.cmd == "ingest-file":
        from pathlib import Path
        from app.documents import ingest_file
        with SessionLocal() as db:
            print("Pending human approval:", ingest_file(db, Path(args.path)))
    elif args.cmd == "crawl-url":
        from app.config import settings
        html = fetch_authorized(args.url, settings.allowed_hosts)
        with SessionLocal() as db:
            print(ingest_page(db, args.url, html))

if __name__ == "__main__":
    main()
