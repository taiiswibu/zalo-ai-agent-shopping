"""Regression tests for natural-language product ranking after guided shopping."""
from datetime import datetime, timezone
from sqlalchemy import select
from app.agent import respond
from app.catalog_advisor import seed_headphones
from app.models import BuyerSession, CompareModel, RetailerListing, RetailerPriceSnapshot

QUESTION = 'Trong 3 mẫu đó, mẫu nào đáng mua nhất? Giải thích lý do'


def guided_flow(db, chat_id='buyer', budget='500k'):
    final = None
    for q in ('tui muốn mua tai nghe', budget, 'Bluetooth nhét tai', 'nghe nhạc', 'ưu tiên chống ồn', 'Android'):
        final = respond(db, chat_id, q)
    return final


def test_best_choice_with_links_and_grounded_anc(db):
    seed_headphones(db)
    final = guided_flow(db)
    assert final.intent == 'advisor_recommend'
    s = db.get(BuyerSession, 'buyer')
    assert len(s.shortlisted_keys) > 0
    response = respond(db, 'buyer', QUESTION)
    assert response.intent == 'advisor_best_choice'
    assert 'R50i NC' in response.text  # ANC prioritized
    assert 'chống ồn ANC' in response.text
    assert 'nghe nhạc' in response.text
    assert 'https://' in response.text
    assert 'CHƯA CÓ GIÁ ĐƯỢC XÁC MINH' in response.text
    assert 'chắc chắn nằm trong ngân sách' in response.text
    assert response.evidence and all(link.startswith('https://') for link in response.evidence)


def test_conversational_phrases_supported(db):
    seed_headphones(db)
    guided_flow(db, 'phrases')
    for prompt in ('Mẫu nào tốt nhất?', 'Nên chọn mẫu nào?', 'Tôi nên mua cái nào?',
                   'trong 3 mẫu đó thì mẫu nào phù hợp nhất?', 'Trong ba mẫu đó?'):
        r = respond(db, 'phrases', prompt)
        assert r.intent == 'advisor_best_choice', (prompt, r.text)


def test_no_candidates_no_fake_recommendation(db):
    r = guided_flow(db, 'unseeded')
    assert r.intent == 'advisor_no_match'
    session = db.get(BuyerSession, 'unseeded')
    assert session.stage == 'no_match'
    assert not session.shortlisted_keys
    result = respond(db, 'unseeded', QUESTION)
    assert result.intent == 'advisor_no_shortlist'
    assert 'chưa có danh sách 3 mẫu' in result.text
    assert 'https://' not in result.text
    assert 'so sánh 1' not in result.text


def test_recovers_after_data_seeded(db):
    guided_flow(db, 'recover')
    seed_headphones(db)
    repick = respond(db, 'recover', 'gợi ý lại')
    assert repick.intent == 'advisor_recommend'
    result = respond(db, 'recover', QUESTION)
    assert result.intent == 'advisor_best_choice'
    assert 'https://' in result.text


def test_price_verified_does_not_override_anc_priority(db):
    seed_headphones(db)
    model = db.scalar(select(CompareModel).where(CompareModel.model_key == 'ANKER-A20I-A3948-NEW'))
    cheap_listing = db.scalar(select(RetailerListing).where(RetailerListing.model_id == model.id))
    db.add(RetailerPriceSnapshot(listing_id=cheap_listing.id, price_vnd=190000,
                page_product_name=model.name, method='manual_review',
                observed_at=datetime.now(timezone.utc)))
    db.commit()
    guided_flow(db, 'price')
    result = respond(db, 'price', QUESTION)
    assert result.intent == 'advisor_best_choice'
    assert 'R50i NC' in result.text
    assert 'chưa thể khẳng định rẻ nhất' in result.text


def test_per_buyer_shortlist_isolation(db):
    seed_headphones(db)
    guided_flow(db, 'one', '500k')
    guided_flow(db, 'two', '1 triệu')
    a = respond(db, 'one', QUESTION)
    b = respond(db, 'two', QUESTION)
    assert a.intent == 'advisor_best_choice' and b.intent == 'advisor_best_choice'
    assert db.get(BuyerSession, 'one').budget_max == 500000
    assert db.get(BuyerSession, 'two').budget_max == 1000000
