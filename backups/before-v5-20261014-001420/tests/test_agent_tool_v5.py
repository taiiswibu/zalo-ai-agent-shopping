"""These tests verify adapter/guardrails offline; LangGraph runtime test skipped if absent."""
import json
from sqlalchemy import select
from app.config import settings
from app.models import AgentToolTrace, BuyerSession, Order, Product
from app.advanced.tool_agent import (ALLOWED_NAMES, GeminiPlanner, FUNCTION_DECLARATIONS,
    render_grounded_outputs, history_for_llm)
from app.commerce import AgentResult
from app.advanced.flow import execute


def test_allowlist_never_exposes_writes():
    assert len(ALLOWED_NAMES) == 6
    assert "confirm_order" not in ALLOWED_NAMES
    assert "place_order" not in ALLOWED_NAMES
    assert "execute_sql" not in ALLOWED_NAMES
    assert "crawl_url" not in ALLOWED_NAMES
    for desc in FUNCTION_DECLARATIONS:
        assert desc["name"] in ALLOWED_NAMES


def test_gemini_planner_parses_valid_multiple_tools(monkeypatch):
    seen = {}
    class Resp:
        def raise_for_status(self):pass
        def json(self):
            return {"candidates":[{"content":{"role":"model","parts":[
                {"functionCall":{"name":"get_shopping_preferences","args":{}}},
                {"functionCall":{"name":"recommend_products","args":{"request":"tai nghe 500k"}}},
            ]}}]}
    def fake_post(url, **kwargs):
        seen.update(kwargs)
        assert url.startswith('https://generativelanguage.googleapis.com/')
        return Resp()
    monkeypatch.setattr("app.advanced.tool_agent.httpx.post",fake_post)
    calls, raw = GeminiPlanner(api_key="fake-for-tests",model="gemini-2.5-flash-lite").plan([{"role":"user","parts":[{"text":"tai nghe 500k"}]}])
    assert len(calls) == 2 and calls[0]["name"] == "get_shopping_preferences"
    assert len({c['id'] for c in calls}) == 2
    assert seen['json']['tools'][0]['functionDeclarations'] == FUNCTION_DECLARATIONS
    assert seen['headers']['x-goog-api-key'] == 'fake-for-tests'


def test_unknown_and_oversized_tool_calls_filtered(monkeypatch):
    class Resp:
        def raise_for_status(self):pass
        def json(self):
            return {"candidates":[{"content":{"role":"model","parts":[
                {"functionCall":{"name":"execute_shell","args":{"command":"rm -rf /"}}},
                {"functionCall":{"name":"search_products","args":{"query":"x"*900}}},
                {"functionCall":{"name":"compare_prices","args":{"model_or_request":{"nested":"no"}}}}
            ]}}]}
    monkeypatch.setattr("app.advanced.tool_agent.httpx.post",lambda *a,**kw: Resp())
    calls,_=GeminiPlanner(api_key="not-real").plan([{"role":"user","parts":[{"text":"a"}]}])
    assert len(calls) == 1 and len(calls[0]['args']['query']) == 350


def test_canonical_renderer_keeps_evidence_not_untrusted_llm():
    output = render_grounded_outputs([
        {"text":"Đã duyệt: 12 tháng", "intent":"policy","evidence":["policy/warranty"]},
        {"text":"TGDD: https://example.com/abc", "intent":"comparison","evidence":["https://example.com/abc"]},
    ])
    assert "12 tháng" in output.text and "https://example.com/abc" in output.text
    assert len(output.evidence) == 2
    assert render_grounded_outputs([{"text":"{}", "intent":"preferences","evidence":[]}]) is None


def test_history_redacts_private_data(db):
    from app.models import ChatEvent
    db.add(ChatEvent(provider_event_id="old-11",chat_id="person-a", query="gọi 0912345678 hoặc test@example.com",reply="ok",status="sent"))
    db.add(ChatEvent(provider_event_id="old-12",chat_id="person-b", query="khác",reply="ok",status="sent"))
    db.commit()
    hist = history_for_llm(db,"person-a")
    text = json.dumps(hist,ensure_ascii=False)
    assert '0912345678' not in text and 'test@example.com' not in text
    assert "khác" not in text


def test_transactional_commands_bypass_gemini(db, monkeypatch):
    monkeypatch.setattr("app.advanced.flow.settings", type('C',(),{'llm_provider':'gemini','gemini_api_key':'fake'})())
    monkeypatch.setattr('app.advanced.tool_agent.execute_tool_agent', lambda *args: (_ for _ in ()).throw(AssertionError('LLM must not execute')))
    result,engine=execute(db,'buyer-x','/buy TAI-001 1')
    assert result.intent == 'draft_order' and engine == 'safe_commerce'
    order=db.scalar(select(Order).where(Order.chat_id=='buyer-x'))
    assert order.status=='draft'
    result,engine=execute(db,'buyer-x','/confirm '+order.code)
    assert result.intent == 'order_confirmed' and engine == 'safe_commerce'


def test_api_failure_safely_falls_back(db, monkeypatch):
    import httpx
    monkeypatch.setattr("app.advanced.flow.settings", type('C',(),{'llm_provider':'gemini','gemini_api_key':'fake'})())
    monkeypatch.setattr('app.advanced.tool_agent.execute_tool_agent',lambda *a: (_ for _ in ()).throw(httpx.TimeoutException('network timeout')))
    result,engine=execute(db,'new-buyer','tai nghe dưới 1 triệu')
    assert result.text and engine in ('langgraph','fallback')


def test_tool_trace_table_exists(db):
    from app.models import AgentToolTrace
    db.add(AgentToolTrace(chat_id='test',tool_name='search_products',result_intent='product_search'))
    db.commit()
    assert db.scalar(select(AgentToolTrace).where(AgentToolTrace.chat_id=='test')).tool_name=='search_products'


def test_real_langgraph_toolnode_offline(db, monkeypatch):
    import pytest
    pytest.importorskip('langgraph')
    pytest.importorskip('langchain_core')
    from app.advanced import tool_agent
    calls=[]
    def fake_plan(self, transcript):
        calls.append(len(transcript))
        if len(calls)==1:
            return ([{'name':'search_products','args':{'query':'tai nghe','max_price_vnd':1000000},'id':'test-call'}],
                    {'role':'model','parts':[{'functionCall':{'name':'search_products','args':{'query':'tai nghe','max_price_vnd':1000000}}}]})
        return [], {'role':'model','parts':[{'text':'Đã nhận thông tin'}]}
    monkeypatch.setattr(tool_agent.GeminiPlanner,'plan',fake_plan)
    result=tool_agent.execute_tool_agent(db,'buyer-v5','tìm tai nghe dưới 1 triệu')
    assert 'TAI-001' in result.text
    assert len(calls)==2
    assert db.scalar(select(AgentToolTrace).where(AgentToolTrace.chat_id=='buyer-v5')) is not None
