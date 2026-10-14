from datetime import datetime, timedelta, timezone
from sqlalchemy import select
import pytest
from app.agent import respond
from app.buyer_advisor import parse_budget, detect_priority, wants_advice
from app.catalog_advisor import seed_headphones
from app.models import BuyerSession, BuyerProductSpec, CompareModel, RetailerListing, RetailerPriceSnapshot


def test_money_parser():
    assert parse_budget('500k') == (None,500000)
    assert parse_budget('dưới 1 triệu') == (None,1000000)
    assert parse_budget('tôi có 1.5 triệu') == (None,1500000)
    assert parse_budget('tôi có 1tr5') == (None,1500000)
    assert parse_budget('500k đến 1tr5') == (500000,1500000)
    assert parse_budget('500',bare_budgets=True) == (None,500000)
    assert parse_budget('xin chào') == (None,None)
    assert detect_priority('tai nghe') == ''
    assert detect_priority('pin lâu') == 'battery'


def test_import_real_product_links(db):
    data=seed_headphones(db)
    assert data['listings_created']>=13
    assert db.query(BuyerProductSpec).count()>=9
    # All sources have URLs, model IDs, but no invented live prices.
    assert db.query(RetailerPriceSnapshot).count()==0
    assert all(x.url.startswith('https://') for x in db.scalars(select(RetailerListing)))
    data2=seed_headphones(db)
    assert data2['listings_created']==0


def test_guided_buyer_flow_link_and_price_uncertainty(db):
    seed_headphones(db)
    answers=[]
    for q in ['tôi muốn mua tai nghe','500k','nhét tai Bluetooth','nghe nhạc','chống ồn','iPhone']:
        answers.append(respond(db,'chat-A',q))
    assert [x.intent for x in answers]==['advisor_budget','advisor_style','advisor_usage','advisor_priority','advisor_device','advisor_recommend']
    assert 'Giá: CHƯA XÁC MINH' in answers[-1].text
    assert 'https://' in answers[-1].text
    assert 'rẻ nhất' not in answers[-1].text.lower().replace('loại nào rẻ nhất','')
    assert respond(db,'chat-A','gửi link').intent=='advisor_links'
    assert respond(db,'chat-A','so sánh 1').intent=='price_comparison'
    assert respond(db,'chat-A','loại nào rẻ nhất').intent=='advisor_unverified'
    assert db.get(BuyerSession,'chat-B') is None


def test_update_budget_and_restart(db):
    seed_headphones(db)
    for q in ['muốn mua tai nghe','600k','chụp tai','họp online','pin lâu','laptop']:
        result=respond(db,'buyer',q)
    assert result.intent=='advisor_recommend'
    assert all('Sony WH-CH520' in result.text or 'JBL Tune' in result.text for _ in (0,))
    r=respond(db,'buyer','đổi ngân sách 1 triệu')
    assert r.intent=='advisor_recommend'
    assert db.get(BuyerSession,'buyer').budget_max==1000000
    assert respond(db,'buyer','tư vấn lại').intent=='advisor_start'
    assert respond(db,'buyer','tai nghe').intent=='advisor_budget'


def test_fresh_price_filter_and_compare(db):
    seed_headphones(db)
    model=db.scalar(select(CompareModel).where(CompareModel.model_key=='ANKER-R50INC-A3959-NEW'))
    li=db.scalars(select(RetailerListing).where(RetailerListing.model_id==model.id)).all()
    assert len(li)==2
    for listing,price in zip(li,[480000,460000]):
        db.add(RetailerPriceSnapshot(listing_id=listing.id,price_vnd=price,page_product_name='Soundcore R50i NC A3959',method='manual_review',observed_at=datetime.now(timezone.utc)))
    db.commit()
    for q in ['muốn mua tai nghe','500k','TWS','nghe nhạc','chống ồn','Android']:
        result=respond(db,'abc',q)
    assert '460.000đ' in result.text
    assert 'ANKER-R50INC-A3959-NEW' in db.get(BuyerSession,'abc').shortlisted_keys
    assert respond(db,'abc','loại nào rẻ nhất').intent == 'advisor_lowest'


def test_stale_price_not_claimed(db):
    seed_headphones(db)
    model=db.scalar(select(CompareModel).where(CompareModel.model_key=='ANKER-R50INC-A3959-NEW'))
    li=db.scalar(select(RetailerListing).where(RetailerListing.model_id==model.id))
    db.add(RetailerPriceSnapshot(listing_id=li.id,price_vnd=300000,page_product_name='Soundcore R50i NC A3959',method='manual_review',observed_at=datetime.now(timezone.utc)-timedelta(days=4)))
    db.commit()
    for q in ['muốn mua tai nghe','500k','TWS','nghe nhạc','chống ồn','Android']:
        r=respond(db,'abc2',q)
    assert '300.000đ' not in r.text


def test_commands_preserved_in_active_session(db):
    seed_headphones(db)
    respond(db,'a','muốn mua tai nghe')
    r=respond(db,'a','/products')
    assert r.intent=='catalog'
    assert respond(db,'a','/compare SONY-WHCH520-NEW').intent=='price_comparison'


def test_phone_buyer_has_correct_variant(db):
    seed_headphones(db)
    a=respond(db,'phone','Tôi muốn mua điện thoại')
    assert a.intent=='advisor_budget'
    b=respond(db,'phone','10 triệu')
    assert b.intent=='advisor_recommend'
    assert 'Galaxy A56' in b.text
    assert 'https://' in b.text


def test_isolation_buyer(db):
    seed_headphones(db)
    respond(db,'one','tôi cần mua tai nghe dưới 1 triệu')
    respond(db,'two','tôi cần mua tai nghe dưới 500k')
    assert db.get(BuyerSession,'one').budget_max == 1000000
    assert db.get(BuyerSession,'two').budget_max == 500000


def test_device_question_saved_and_multi_slot_first_message(db):
    seed_headphones(db)
    first = respond(db, 'multi', 'Tôi muốn mua tai nghe 1 triệu, chụp tai, họp online, pin lâu, dùng iPhone')
    assert first.intent == 'advisor_recommend'
    s = db.get(BuyerSession, 'multi')
    assert s.budget_max == 1_000_000
    assert s.style == 'over_ear'
    assert s.use_case == 'calls'
    assert s.priority == 'battery'
    assert s.device == 'apple'
    assert 'https://' in first.text


def test_advisor_no_fake_prices(db):
    seed_headphones(db)
    for line in ['muốn mua tai nghe', '700k', 'TWS', 'nghe nhạc', 'pin lâu', 'laptop']:
        r=respond(db,'no-prices',line)
    assert r.intent == 'advisor_recommend'
    assert 'CHƯA XÁC MINH' in r.text
    assert 'https://' in r.text
    assert 'rẻ nhất là' not in r.text


def test_complete_url_formatter():
    from app.buyer_advisor import _message_with_complete_urls
    text=_message_with_complete_urls(['Header','https://example.com/'+'a'*80]*20,max_chars=250)
    assert not text.endswith('a')
    assert 'gửi link' in text


def test_no_expensive_anc_model_on_500k_budget(db):
    seed_headphones(db)
    seq=['muốn mua tai nghe','500k','tws','nghe nhạc','chống ồn','android']
    for q in seq: r=respond(db,'b500',q)
    assert 'Sony WF-C710N' not in r.text
    assert 'Soundcore R50i' in r.text
    assert 'CHƯA XÁC MINH' in r.text
