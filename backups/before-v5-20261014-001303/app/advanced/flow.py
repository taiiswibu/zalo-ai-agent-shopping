"""Actual LangGraph StateGraph when installed, deterministic fallback otherwise.

LLM never executes SQL or places orders. Business commands are handled by the
original validated v1 commerce module, which retains existing SQLite records.
"""
from __future__ import annotations
import logging
import re
import httpx
from typing import Any, TypedDict
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Product, ChatContext, BuyerSession
from app.retrieval import normalize, find_products
from app.commerce import AgentResult, respond as commerce_respond, render_product, price_ceiling
from app.rag import search as rag_search
from app.ai import generate
from app.config import settings
from app.price_compare import compare as price_compare
from app.buyer_advisor import wants_advice, advise

log = logging.getLogger(__name__)
GRAPH_AVAILABLE = False

class BotState(TypedDict, total=False):
    db: Session
    chat_id: str
    question: str
    route: str
    result: AgentResult


def route(state: BotState) -> dict:
    question = state["question"].strip()
    q = normalize(question).strip()
    if "db" in state and "chat_id" in state and wants_advice(state["db"], state["chat_id"], question):
        return {"route": "advisor"}
    if (question.lower().startswith("/compare") or
        "so sanh gia" in q or "gia o dau" in q or "noi nao re" in q or
        "cho nao re" in q or "mua o dau re" in q or "gia ben nao" in q or
        ("gia" in q and any(v in q for v in ("cellphones", "the gioi di dong", "tgdd", "hoang ha", "fptshop", "viettel store")))):
        return {"route": "comparison"}
    if (question.startswith("/") or q in {"xin chao", "hi", "hello", "help", "start"}):
        return {"route": "order"}
    if any(k in q for k in ("bao hanh", "doi tra", "hoan tien", "giao hang", "van chuyen", "ship", "chinh sach", "huong dan su dung")):
        return {"route": "policy"}
    return {"route": "sales"}


def advisor_node(state: BotState) -> dict:
    return {"result": advise(state["db"], state["chat_id"], state["question"])}


def compare_node(state: BotState) -> dict:
    return {"result": price_compare(state["db"], state["question"])}


def order_node(state: BotState) -> dict:
    return {"result": commerce_respond(state["db"], state["chat_id"], state["question"])}


def sales_node(state: BotState) -> dict:
    db = state["db"]
    question = state["question"].strip()
    normalized = normalize(question)
    # Never use the LLM to authorize a purchase or calculate business totals.
    if not question:
        return {"result": commerce_respond(db, state["chat_id"], question)}
    ctx = db.get(ChatContext, state["chat_id"])
    followup = any(key in normalized for key in ("loai re nhat", "cai re nhat", "cai nao re", "mau re nhat", "loai nao re"))
    if followup and ctx and ctx.last_product_skus:
        candidates = db.scalars(select(Product).where(Product.sku.in_(ctx.last_product_skus), Product.stock > 0)).all()
        if candidates:
            p = min(candidates, key=lambda r: (r.price_vnd, r.sku))
            return {"result": AgentResult("Trong các mẫu vừa đề xuất, rẻ nhất là:\n" + render_product(p) + "\nMua: /buy " + p.sku + " 1", "followup", [p.sku])}
    # Exact SKU handled by original v1 with source of truth.
    if re.search(r"\b(?:TAI|SAC|CAP|CHU|PIN)-\d{3}\b", question.upper()):
        return {"result": commerce_respond(db, state["chat_id"], question)}
    price = price_ceiling(question)
    items = find_products(db, question, max_price=price, in_stock_only=True, limit=4)
    if not items:
        return {"result": commerce_respond(db, state["chat_id"], question)}
    record = ctx or ChatContext(chat_id=state["chat_id"])
    record.last_product_query = question[:350]
    record.last_product_skus = [p.sku for p in items]
    db.add(record)
    db.commit()
    # LLM writes a short friendly sentence, but canonical facts are always appended from SQL.
    facts = "\n".join(f"{p.sku}: {p.name} — {p.description[:140]}" for p in items)
    intro = generate(question, facts, instruction="Chỉ viết một câu dẫn 15-40 từ. KHÔNG nhắc số tiền, tồn kho, SKU, bảo hành, thông số định lượng hoặc URL; phần sản phẩm sẽ ghép bằng SQL.")
    if intro and (re.search(r"\d|\b(?:vnd|đồng|triệu|nghìn|kho|bảo hành)\b|https?://", intro, flags=re.I)):
        intro = None
    heading = intro or "Mình tìm được những sản phẩm phù hợp với yêu cầu:"
    answer = heading + "\n" + "\n".join(render_product(p) for p in items) + "\nGõ /buy SKU 1 để tạo đơn nháp."
    return {"result": AgentResult(answer, "product_search", [p.sku for p in items])}


def policy_node(state: BotState) -> dict:
    db = state["db"]
    question = state["question"].strip()
    hits = rag_search(db, question, limit=3)
    if not hits:
        return {"result": AgentResult("Chưa có tài liệu đã duyệt phù hợp. Bạn vui lòng hỏi nhân viên cửa hàng.", "policy_unknown", [])}
    context = "\n".join(f"[{h.key}] {h.title}: {h.text}" for h in hits)
    answer = generate(question, context)
    # Always append verifiable snippets; an LLM summary alone is not sufficient evidence.
    snippets = "\n".join(f"• [{h.key}] {h.title}: {h.text[:220]}" for h in hits[:2])
    sources = "Nguồn:\n" + "\n".join(f"[{h.key}] {h.url or 'tài liệu nội bộ'}" for h in hits)
    result = (answer + "\n\n" if answer else "Theo tài liệu đã duyệt:\n") + snippets + "\n" + sources
    return {"result": AgentResult(result[:1900], "policy", list(dict.fromkeys(h.key for h in hits)))}


def _fallback(state: BotState) -> dict:
    state = {**state, **route(state)}
    chosen = {"order": order_node, "policy": policy_node, "sales": sales_node, "comparison": compare_node, "advisor": advisor_node}[state["route"]]
    return {**state, **chosen(state)}


def _make_graph():
    try:
        from langgraph.graph import StateGraph, START, END
    except ImportError:
        return None
    graph = StateGraph(BotState)
    graph.add_node("router", route)
    graph.add_node("sales", sales_node)
    graph.add_node("policy", policy_node)
    graph.add_node("orders", order_node)
    graph.add_node("comparison", compare_node)
    graph.add_node("advisor", advisor_node)
    graph.add_edge(START, "router")
    graph.add_conditional_edges("router", lambda s: s["route"],
                                {"sales": "sales", "policy": "policy", "order": "orders", "comparison": "comparison", "advisor": "advisor"})
    for node in ("sales", "policy", "orders", "comparison", "advisor"):
        graph.add_edge(node, END)
    return graph.compile()


try:
    _GRAPH = _make_graph()
except Exception as exc:
    log.warning("LangGraph initialization failed: %s", type(exc).__name__)
    _GRAPH = None
GRAPH_AVAILABLE = _GRAPH is not None


def execute(db: Session, chat_id: str, question: str) -> tuple[AgentResult, str]:
    # Explicit transactional commands ALWAYS stay in validated commerce logic.
    # Gemini sees only read-only tools; it cannot create or confirm any order.
    q = question.strip()
    if q.lower().startswith("/compare"):
        return price_compare(db, question), "safe_comparison"
    if q.startswith("/") and not q.lower().startswith(("/tuvan", "/goiy")):
        return commerce_respond(db, chat_id, question), "safe_commerce"
    # Short replies to ongoing questionnaire must advance exactly one stored step;
    # do not pay a model call to guess what "500k" means.
    existing = db.get(BuyerSession, chat_id)
    if existing and existing.stage in ("category", "budget", "style", "usage", "priority", "device"):
        return advise(db, chat_id, question), "advisor_state"
    if q.lower().startswith(("/tuvan", "/goiy")):
        return advise(db, chat_id, question), "advisor_state"
    # Gemini function calling is enabled ONLY when explicitly configured.
    if settings.llm_provider == "gemini" and settings.gemini_api_key:
        try:
            from app.advanced.tool_agent import execute_tool_agent
            result = execute_tool_agent(db, chat_id, question)
            if result is not None:
                return result, "gemini_langgraph_tools"
        except (ImportError, httpx.HTTPError, KeyError, ValueError, TypeError, RuntimeError) as exc:
            log.warning("Tool agent unavailable; safe fallback: %s", type(exc).__name__)
            db.rollback()
    data = {"db": db, "chat_id": chat_id, "question": question}
    if _GRAPH is not None:
        result = _GRAPH.invoke(data)
        return result["result"], "langgraph"
    return _fallback(data)["result"], "fallback"
