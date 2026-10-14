from sqlalchemy import select
from app.agent import respond, price_ceiling
from app.models import Order, Product


def test_pricing_parser():
    assert price_ceiling("tai nghe dưới 1 triệu") == 1_000_000
    assert price_ceiling("sạc không quá 500k") == 500_000


def test_search_grounded(db):
    r = respond(db, "a", "tai nghe dưới 1 triệu")
    assert "TAI-001" in r.text and "TAI-002" in r.text
    assert "source" not in r.evidence
    assert "CHU-001" not in r.text


def test_policy_citation(db):
    r = respond(db, "a", "bảo hành sao shop")
    assert "Nguồn:" in r.text and "warranty" in r.text


def test_order_owner_and_stock(db):
    before = db.scalar(select(Product).where(Product.sku == "TAI-001")).stock
    draft = respond(db, "buyer-1", "/buy TAI-001 2")
    code = db.scalar(select(Order).where(Order.chat_id == "buyer-1")).code
    assert "NHÁP" in draft.text
    denied = respond(db, "buyer-2", "/confirm " + code)
    assert "Không tìm thấy" in denied.text
    ok = respond(db, "buyer-1", "/confirm " + code)
    assert "Đã ghi nhận" in ok.text
    assert db.scalar(select(Product).where(Product.sku == "TAI-001")).stock == before - 2
    duplicate = respond(db, "buyer-1", "/confirm " + code)
    assert "confirmed" in duplicate.text
    assert db.scalar(select(Product).where(Product.sku == "TAI-001")).stock == before - 2


def test_cannot_buy_scraped_or_out_of_stock(db):
    assert "không đủ tồn kho" in respond(db, "a", "/buy CHU-001 1").text
    assert "Không có SKU" in respond(db, "a", "/buy UNKNOWN 1").text


def test_cancel_is_only_draft(db):
    respond(db, "a", "/buy CAP-001 1")
    code = db.scalar(select(Order).where(Order.chat_id == "a")).code
    assert "Chỉ hủy" in respond(db, "b", "/cancel " + code).text
    assert "Đã hủy" in respond(db, "a", "/cancel " + code).text
    assert "Chỉ hủy" in respond(db, "a", "/cancel " + code).text
