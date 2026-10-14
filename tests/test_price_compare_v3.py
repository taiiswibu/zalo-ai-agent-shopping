from pathlib import Path
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
import pytest
from app.db import init_db
from app.models import CompareModel, RetailerListing, RetailerPriceSnapshot, Product, Order
from app.price_compare import (
    import_watchlist, parse_retail_html, import_reviewed_prices, compare, variant_match,
    canonical_url, crawl_watchlist
)
from app.agent import respond

WATCH = Path(__file__).parent.parent / "examples/retailer_watchlist.csv"


def _model(db):
    import_watchlist(db, WATCH)
    return db.scalar(select(CompareModel).where(CompareModel.model_key == "SAMSUNG-A56-8-128-NEW"))


def _jsonld(name, price, currency="VND"):
    return '<html><head><script type="application/ld+json">' + (
        '{"@context":"https://schema.org","@type":"Product","name":' +
        __import__('json').dumps(name) + ',"offers":{"@type":"Offer","price":' +
        __import__('json').dumps(price) + ',"priceCurrency":' +
        __import__('json').dumps(currency) + '}}') + '</script></head><body><h1>' + name + '</h1></body></html>'


def test_source_registry(db):
    assert import_watchlist(db, WATCH) == (3, 0)
    assert import_watchlist(db, WATCH) == (0, 3)
    assert db.query(RetailerListing).count() == 3
    with pytest.raises(ValueError):
        canonical_url("https://evil.com/dtdd/x", "tgdd")
    with pytest.raises(ValueError):
        canonical_url("https://cellphones.com.vn/p?variant=12-256", "cellphones")


def test_exact_variant_matching(db):
    m = _model(db)
    assert variant_match(m, "Điện thoại Samsung Galaxy A56 5G 8GB/128GB")[0]
    for bad in (
        "Samsung Galaxy A56 5G 12GB/256GB",
        "Samsung Galaxy A56 5G 8GB/256GB",
        "Samsung Galaxy A56 5G 8GB/128GB - Cũ đẹp",
        "Samsung Galaxy A56 5G 8GB/128GB - Đã kích hoạt",
        "Samsung Galaxy A55 5G 8GB/128GB",
        "Samsung Galaxy A56 5G",
    ):
        assert not variant_match(m, bad)[0], bad


def test_jsonld_verified(db):
    m = _model(db)
    page = _jsonld("Samsung Galaxy A56 5G 8GB/128GB", "8690000")
    assert parse_retail_html(m,"https://cellphones.com.vn/dien-thoai-samsung-galaxy-a56.html", page)[1:] == (8690000, "jsonld")
    with pytest.raises(ValueError):
        parse_retail_html(m, "https://cellphones.com.vn/dien-thoai-samsung-galaxy-a56.html", _jsonld("Samsung Galaxy A56 5G 8GB/256GB", "6900000"))
    with pytest.raises(ValueError):
        parse_retail_html(m, "https://cellphones.com.vn/dien-thoai-samsung-galaxy-a56.html", _jsonld("Samsung Galaxy A56 5G 8GB/128GB", "8690000", "USD"))


def test_meta_only_with_h1(db):
    m = _model(db)
    html = '<html><head><meta property="product:price:amount" content="8,690,000"/></head><body><h1>Samsung Galaxy A56 5G 8GB/128GB</h1></body></html>'
    assert parse_retail_html(m,"https://hoanghamobile.com/dien-thoai/samsung-galaxy-a56-5g-8gb-128gb",html)[1:] == (8690000, "meta")
    with pytest.raises(ValueError):
        parse_retail_html(m,"https://hoanghamobile.com/dien-thoai/samsung-galaxy-a56-5g-8gb-128gb", html.replace('8GB/128GB','8GB/256GB'))


def test_no_invented_prices_with_links(db):
    _model(db)
    result = compare(db, "/compare SAMSUNG-A56-8-128-NEW")
    assert "chưa có giá xác minh" in result.text.lower()
    assert "https://cellphones.com.vn/dien-thoai-samsung-galaxy-a56.html" in result.text
    assert "https://www.thegioididong.com/dtdd/samsung-galaxy-a56-8gb-128gb" in result.text
    assert "https://hoanghamobile.com/dien-thoai/samsung-galaxy-a56-5g-8gb-128gb" in result.text
    assert "rẻ nhất" not in result.text.lower()


def test_reviewed_price_ranking_plus_zalo_agent(db, tmp_path):
    _model(db)
    stores = db.scalars(select(RetailerListing).order_by(RetailerListing.retailer)).all()
    reviewed = tmp_path / "verified.csv"
    reviewed.write_text("url,page_product_name,price_vnd,verified_by_human\n" + "\n".join(
        f"{s.url},Samsung Galaxy A56 5G 8GB/128GB,{p},yes" for s,p in zip(stores,(8990000,8790000,9190000))
    ) + "\n", encoding="utf-8")
    assert import_reviewed_prices(db, reviewed) == 3
    text = respond(db, "customer_1", "/compare SAMSUNG-A56-8-128-NEW").text
    assert "Giá thấp nhất" in text
    assert "8.790.000đ" in text
    assert "https://" in text
    assert db.query(RetailerPriceSnapshot).count() == 3
    assert db.query(Product).count() == 6  # no corruption of shop-owned catalog
    assert db.query(Order).count() == 0  # comparison never books order


def test_stale_prices_not_current(db):
    _model(db)
    listing = db.scalars(select(RetailerListing)).first()
    db.add(RetailerPriceSnapshot(listing_id=listing.id, price_vnd=8000000,
                                 page_product_name="Samsung Galaxy A56 5G 8GB/128GB",method="manual_review",
                                 observed_at=datetime.now(timezone.utc) - timedelta(days=5)))
    db.commit()
    out = compare(db, "so sánh giá Samsung A56 8GB/128GB")
    assert "Chưa có giá kiểm tra trong 24h" in out.text
    assert "Giá thấp nhất TRONG" not in out.text


def test_comparison_refuses_wrong_query(db):
    _model(db)
    assert "Chưa xác định" in compare(db, "so sánh Samsung A56 8GB/256GB").text
    assert "Chưa xác định" in compare(db, "so sánh Samsung A55 8GB/128GB").text


def test_crawl_requires_permission(db):
    _model(db)
    with pytest.raises(ValueError):
        crawl_watchlist(db)


def test_manual_price_requires_review(db, tmp_path):
    _model(db)
    listing = db.scalars(select(RetailerListing)).first()
    filename = tmp_path / 'reviewed.csv'
    filename.write_text(f'url,page_product_name,price_vnd,verified_by_human\n{listing.url},Samsung Galaxy A56 5G 8GB/128GB,7990000,no\n')
    with pytest.raises(ValueError):
        import_reviewed_prices(db, filename)


def test_accessories_exact_mpn(db, tmp_path):
    csvfile = tmp_path / 'watch.csv'
    csvfile.write_text('model_key,name,brand,model_token,ram_gb,storage_gb,condition,retailer,url,crawl_enabled\n'
                       'ANKER-A1263-NEW,Anker Power Bank A1263,Anker,A1263,0,0,new,cellphones,https://cellphones.com.vn/anker-a1263.html,false\n', encoding='utf-8')
    assert import_watchlist(db, csvfile) == (1, 0)
    model = db.scalar(select(CompareModel).where(CompareModel.model_key == 'ANKER-A1263-NEW'))
    assert variant_match(model, 'Pin dự phòng Anker A1263 chính hãng')[0]
    assert not variant_match(model, 'Pin dự phòng Anker A1262 chính hãng')[0]
    assert not variant_match(model, 'Pin dự phòng Anker A1263 cũ')[0]
    assert 'MPN A1263' in compare(db, '/compare ANKER-A1263-NEW').text


def test_price_parser_decimal_format(db):
    from app.price_compare import _price_number
    assert _price_number('8690000.00') == 8690000
    assert _price_number('8.690.000') == 8690000
    assert _price_number('8,690,000.00') == 8690000
    assert _price_number('not-a-price') is None
    assert _price_number('8690000.50') is None
    assert _price_number('0') is None
