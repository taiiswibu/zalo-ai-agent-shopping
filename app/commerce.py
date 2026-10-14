from __future__ import annotations
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from app.models import Order, Product
from app.retrieval import normalize, find_products, find_knowledge
from app.ai import optional_gemini


def money(v: int) -> str:
    return f"{v:,}".replace(",", ".") + "đ"


def price_ceiling(text: str) -> int | None:
    t = normalize(text)
    m = re.search(r"(?:duoi|toi da|khong qua|tam|khoang)\s+(\d+(?:[.,]\d+)?)\s*(trieu|tr|k|nghin|ngan)?", t)
    if not m:
        return None
    raw = float(m.group(1).replace(",", "."))
    suffix = m.group(2) or ""
    if suffix in {"trieu", "tr"}:
        return round(raw * 1_000_000)
    if suffix in {"nghin", "ngan", "k"}:
        return round(raw * 1000)
    return int(raw) if raw >= 10000 else None


def render_product(p: Product) -> str:
    return f"• [{p.sku}] {p.name}: {money(p.price_vnd)} | Kho: {p.stock} | BH: {p.warranty_months} tháng"

@dataclass
class AgentResult:
    text: str
    intent: str
    evidence: list[str]


def respond(db: Session, chat_id: str, question: str) -> AgentResult:
    raw = question.strip()[:1000]
    if not raw:
        return AgentResult("Bạn hãy nhập câu hỏi dạng văn bản nhé. Gõ /help để xem các lệnh.", "empty", [])
    t = normalize(raw).strip()
    sku_match = re.search(r"\b(?:TAI|SAC|CAP|CHU|PIN)-\d{3}\b", raw.upper())
    sku = sku_match.group() if sku_match else None
    help_text = (
        "🛒 Bot bán phụ kiện điện tử (dữ liệu mẫu)\n"
        "• Gõ: tai nghe dưới 1 triệu / pin dự phòng\n"
        "• /products — xem sản phẩm\n"
        "• /compare — so sánh giá TGDD, CellphoneS, Hoàng Hà\n"
        "• /buy TAI-001 1 — tạo đơn nháp\n"
        "• /confirm MÃ — xác nhận đặt hàng\n"
        "• /cancel MÃ — hủy đơn nháp\n"
        "• /orders — xem đơn của bạn\n"
        "• Hỏi: bảo hành / đổi trả / giao hàng\n"
        "Lưu ý: Không yêu cầu gửi số thẻ, OTP hoặc mật khẩu qua chat."
    )
    if t in {"/start", "start", "help", "/help", "xin chao", "hi", "hello"}:
        return AgentResult(help_text, "help", [])

    if raw.lower().startswith("/products"):
        products = find_products(db, "", limit=15)
        return AgentResult("Danh mục:\n" + "\n".join(render_product(p) for p in products) + "\nMua: /buy SKU 1", "catalog", [p.sku for p in products])

    if raw.lower().startswith("/orders"):
        orders = db.scalars(select(Order).where(Order.chat_id == chat_id).order_by(Order.id.desc()).limit(8)).all()
        return AgentResult("Đơn của bạn:\n" + "\n".join(f"• {o.code}: {o.sku} ×{o.quantity} - {o.status}" for o in orders) if orders else "Bạn chưa có đơn hàng.", "orders", [])

    if raw.lower().startswith("/buy"):
        m = re.fullmatch(r"/buy\s+([\w-]+)\s+(\d{1,2})", raw, flags=re.I)
        if not m:
            return AgentResult("Cú pháp đúng: /buy TAI-001 1", "order_validation", [])
        code, qty = m.group(1).upper(), int(m.group(2))
        if not 1 <= qty <= 10:
            return AgentResult("Chỉ hỗ trợ số lượng 1–10 mỗi lần.", "order_validation", [])
        p = db.scalar(select(Product).where(Product.sku == code))
        if not p or not p.is_authoritative:
            return AgentResult("Không có SKU bán chính thức này. Bạn gõ /products để xem.", "order_validation", [])
        if p.stock < qty:
            return AgentResult("Sản phẩm không đủ tồn kho. Vui lòng chọn số lượng khác.", "order_validation", [p.sku])
        order_code = "OD" + secrets.token_hex(4).upper()
        o = Order(code=order_code, chat_id=chat_id, sku=p.sku, quantity=qty, unit_price_vnd=p.price_vnd, total_vnd=p.price_vnd * qty, status="draft")
        db.add(o)
        db.commit()
        return AgentResult(f"Đơn NHÁP {order_code}\n{p.name} ×{qty}\nTạm tính: {money(o.total_vnd)}\nChưa thanh toán, chưa giữ hàng.\nĐể xác nhận đặt hàng: /confirm {order_code}\nĐể hủy: /cancel {order_code}", "draft_order", [p.sku])

    if raw.lower().startswith("/confirm"):
        m = re.fullmatch(r"/confirm\s+(OD[0-9A-F]{8})", raw, flags=re.I)
        if not m:
            return AgentResult("Cú pháp: /confirm MÃ_ĐƠN", "order_validation", [])
        code = m.group(1).upper()
        order = db.scalar(select(Order).where(Order.code == code, Order.chat_id == chat_id))
        if not order:
            return AgentResult("Không tìm thấy đơn này trong cuộc trò chuyện của bạn.", "order_validation", [])
        if order.status != "draft":
            return AgentResult(f"Đơn {code} đang ở trạng thái {order.status}.", "order_status", [])
        # Row-level update prevents overselling under concurrent confirmations.
        updated = db.execute(update(Product).where(Product.sku == order.sku, Product.stock >= order.quantity, Product.is_authoritative.is_(True)).values(stock=Product.stock - order.quantity))
        if updated.rowcount != 1:
            db.rollback()
            return AgentResult("Số lượng tồn kho đã thay đổi; chưa đặt hàng. Vui lòng liên hệ cửa hàng.", "order_out_of_stock", [])
        p = db.scalar(select(Product).where(Product.sku == order.sku))
        if p.price_vnd != order.unit_price_vnd:
            db.rollback()
            return AgentResult("Giá đã thay đổi, đơn nháp chưa được xác nhận. Vui lòng tạo lại đơn.", "price_changed", [])
        order.status = "confirmed"
        order.confirmed_at = datetime.now(timezone.utc)
        db.commit()
        return AgentResult(f"✅ Đã ghi nhận đơn {code}. Tổng: {money(order.total_vnd)}. Đây là đơn DEMO, chưa có thanh toán/vận chuyển tự động. Nhân viên cần xác nhận giao hàng.", "order_confirmed", [order.sku])

    if raw.lower().startswith("/cancel"):
        m = re.fullmatch(r"/cancel\s+(OD[0-9A-F]{8})", raw, flags=re.I)
        if not m:
            return AgentResult("Cú pháp: /cancel MÃ_ĐƠN", "order_validation", [])
        order = db.scalar(select(Order).where(Order.code == m.group(1).upper(), Order.chat_id == chat_id))
        if not order or order.status != "draft":
            return AgentResult("Chỉ hủy được đơn nháp của chính bạn.", "order_validation", [])
        order.status = "cancelled"
        db.commit()
        return AgentResult(f"Đã hủy đơn nháp {order.code}.", "order_cancelled", [])

    if any(x in t for x in ["bao hanh", "doi tra", "hoan tien", "giao hang", "van chuyen", "ship"]):
        docs = find_knowledge(db, raw, 2)
        if not docs:
            return AgentResult("Chưa có chính sách đã duyệt để trả lời. Vui lòng hỏi nhân viên cửa hàng.", "policy_unknown", [])
        snippets = "\n".join(f"[{d.source_key}] {d.title}: {d.content[:450]}" for d in docs)
        ai = optional_gemini(raw, snippets)
        return AgentResult((ai or snippets) + "\nNguồn: " + ", ".join(f"{d.source_key} ({d.source_url})" for d in docs), "policy", [d.source_key for d in docs])

    if sku:
        p = db.scalar(select(Product).where(Product.sku == sku))
        if p:
            return AgentResult(render_product(p) + "\n" + p.description[:250] + "\nMua: /buy " + p.sku + " 1", "sku", [p.sku])

    limit = price_ceiling(raw)
    candidates = find_products(db, raw, max_price=limit, in_stock_only=True)
    if not candidates and limit is not None:
        return AgentResult(f"Chưa có sản phẩm phù hợp dưới {money(limit)} trong catalog đã xác nhận. Gõ /products để xem danh mục.", "no_product", [])
    if candidates:
        summary = "Mình tìm được:\n" + "\n".join(render_product(p) for p in candidates[:4])
        return AgentResult(summary + "\nHãy gửi SKU để xem chi tiết hoặc /buy SKU 1 để tạo đơn nháp.", "product_search", [p.sku for p in candidates[:4]])
    return AgentResult("Mình chưa có dữ liệu đủ tin cậy để trả lời câu này. Hãy hỏi về sản phẩm, bảo hành, đổi trả hoặc gõ /help.", "unknown", [])
