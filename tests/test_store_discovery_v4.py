from app.store_discovery import discover_links, review_matches
from app.catalog_advisor import seed_headphones


def test_discover_tgdd_filters_duplicates_external_urls(db):
    seed_headphones(db)
    html='''<a href="/tai-nghe/tai-nghe-bluetooth-chup-tai-sony-wh-ch520">Sony WH-CH520 tai nghe chụp tai</a>
    <a href="/tai-nghe/tai-nghe-bluetooth-chup-tai-sony-wh-ch520?utm_source=ads">same</a>
    <a href="https://evil.example/tai-nghe/bad">Sony WH-CH520</a>
    <a href="/gio-hang/checkout">Checkout</a>'''
    rows=discover_links(html,"https://www.thegioididong.com/tai-nghe-sony","tgdd")
    assert len(rows)==1
    assert rows[0]['url']=='https://www.thegioididong.com/tai-nghe/tai-nghe-bluetooth-chup-tai-sony-wh-ch520'
    reviewed=review_matches(db,rows)
    assert reviewed[0]['model_key_guess']=='SONY-WHCH520-NEW'
    assert reviewed[0]['match_status']=='candidate_review_required'


def test_discover_cellphones(db):
    html='''<a href="https://cellphones.com.vn/tai-nghe-chup-tai-sony-wh-ch520.html">Tai nghe Sony WH-CH520</a>
    <a href="/danh-muc">danh mục</a>'''
    rows=discover_links(html,'https://cellphones.com.vn/phu-kien.html','cellphones')
    assert len(rows)==1
