from sqlalchemy import select
from app.agent import respond
from app.models import RagChunk, RagVector, Knowledge, ChatContext, AgentTrace, CrawlObservation
from app.rag import pieces, sync_index, search, cosine
from app.advanced.flow import route
from app.crawler import ingest_page


def test_v2_graph_router():
    assert route({'question':'/buy TAI-001 1'})['route'] == 'order'
    assert route({'question':'bảo hành ra sao'})['route'] == 'policy'
    assert route({'question':'cần tai nghe'})['route'] == 'sales'


def test_chunking_handles_big_text():
    chunks=pieces('Khối dài ' * 300)
    assert len(chunks) >= 2
    assert all(len(c) <= 650 for c in chunks)


def test_rag_approval_revocation(db):
    original=sync_index(db)
    assert db.scalars(select(RagChunk)).first()
    doc = db.scalar(select(Knowledge).where(Knowledge.source_key=='warranty'))
    assert any(h.key=='warranty' for h in search(db, 'bảo hành'))
    doc.approved=False
    db.commit()
    sync_index(db)
    assert not any(h.key=='warranty' for h in search(db, 'bảo hành'))
    assert not db.scalars(select(RagChunk).where(RagChunk.knowledge_id==doc.id)).all()


def test_document_change_invalidates_embedding(db):
    sync_index(db)
    doc=db.scalar(select(Knowledge).where(Knowledge.source_key=='shipping'))
    old=db.scalar(select(RagChunk).where(RagChunk.knowledge_id==doc.id))
    db.add(RagVector(chunk_id=old.id,model='test-embedding',vector=[0.1,0.2]))
    db.commit()
    doc.content='Chính sách vận chuyển đã được quản trị viên phê duyệt phiên bản mới.'
    db.commit()
    sync_index(db)
    assert not db.scalars(select(RagVector).where(RagVector.model=='test-embedding')).all()


def test_followup_and_trace(db):
    r=respond(db, 'customer-1', 'tai nghe dưới 1 triệu')
    assert 'TAI-001' in r.text
    follow=respond(db, 'customer-1','loại rẻ nhất')
    assert 'TAI-001' in follow.text and follow.intent=='followup'
    ctx=db.get(ChatContext,'customer-1')
    assert 'TAI-001' in ctx.last_product_skus
    t=db.scalars(select(AgentTrace).where(AgentTrace.chat_id=='customer-1')).all()
    assert len(t)==2 and all(x.latency_ms>=0 for x in t)


def test_crawl_price_is_observation_not_catalog(db):
    html='''<html><title>Sản phẩm thử nghiệm</title><script type="application/ld+json">{"@type":"Product","name":"Chuột thật","sku":"REAL-123","offers":{"price":"90000","priceCurrency":"VND"}}</script><main><p>Mô tả sản phẩm kiểm thử đủ dài để trích xuất.</p></main></html>'''
    ingest_page(db,'https://example.org/p1',html)
    assert db.scalars(select(CrawlObservation)).first().payload['observed_price']=='90000'
    assert db.scalar(select(Knowledge).where(Knowledge.source_key.like('crawl:%'))).approved is False
    from app.models import Product
    assert not db.scalar(select(Product).where(Product.sku=='REAL-123'))


def test_cosine():
    assert abs(cosine([1,0],[1,0])-1)<1e-9
    assert cosine([1,0],[0,1])==0


def test_gemini_fails_gracefully(monkeypatch,db):
    from app import ai
    class FakeSettings:
        llm_provider='gemini'; gemini_api_key='placeholder'; gemini_model='fake'
        llm_timeout=3; rag_backend='keyword'; embedding_model='fake'
    monkeypatch.setattr(ai, 'settings',FakeSettings())
    def broken(*args,**kwargs):
        raise __import__('httpx').ConnectError('offline')
    monkeypatch.setattr(ai.httpx,'post',broken)
    assert ai.generate('bao hanh','evidence') is None
    r=respond(db,'customer','tai nghe')
    assert 'TAI-001' in r.text
