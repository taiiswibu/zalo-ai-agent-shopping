import pytest
from sqlalchemy import select
from app.crawler import extract_html, ingest_page, validate_target
from app.models import Knowledge

HTML = '''<html><head><title>Demo Tai Nghe</title><script type="application/ld+json">
{"@context":"https://schema.org", "@type":"Product","name":"Tai nghe XYZ", "sku":"X-1",
"description":"Tai nghe mới", "offers":{"price":"100000","priceCurrency":"VND"}}
</script></head><body><main><p>Mô tả công khai về tai nghe có âm thanh tốt.</p></main></body></html>'''


def test_json_ld_parser():
    p = extract_html("https://example.com/product", HTML)
    assert p["product"]["name"] == "Tai nghe XYZ"
    assert p["product"]["observed_price"] == "100000"


def test_ingest_unapproved(db):
    x = ingest_page(db, "https://example.com/product", HTML)
    k = db.scalar(select(Knowledge).where(Knowledge.source_key == x["source_key"]))
    assert k.approved is False


def test_allowlist_blocks_local_and_http():
    with pytest.raises(ValueError):
        validate_target("http://example.com", {"example.com"})
    with pytest.raises(ValueError):
        validate_target("https://127.0.0.1/admin", {"127.0.0.1"})
    with pytest.raises(ValueError):
        validate_target("https://evil.com", {"example.com"})
