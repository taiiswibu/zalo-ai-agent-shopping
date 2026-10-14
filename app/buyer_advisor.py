"""Stateful Vietnamese guided shopping, using *verified* same-variant retailer links.

Does not invent price, present retailer data as owned stock, or place orders.
Buyer flow: category -> budget -> form factor -> usage -> priority -> device -> shortlist.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.commerce import AgentResult, money
from app.models import BuyerSession, BuyerProductSpec, CompareModel, RetailerListing, RetailerPriceSnapshot
from app.price_compare import RETAILERS, compare as compare_price
from app.retrieval import normalize

CATEGORIES = {
    "tai_nghe": ("tai nghe", "earbud", "headphone", "headset", "airpod", "tws"),
    "dien_thoai": ("dien thoai", "smartphone", "iphone", "galaxy"),
    "sac": ("cu sac", "bo sac", "sac nhanh", "adapter"),
    "pin": ("pin du phong", "power bank"),
    "chuot": ("chuot", "mouse"),
    "ban_phim": ("ban phim", "keyboard"),
    "loa": ("loa", "speaker"),
}


def detect_category(query: str) -> str:
    q = normalize(query)
    for cat, aliases in CATEGORIES.items():
        if any(alias in q for alias in aliases):
            return cat
    return ""


def _parse_number(text: str, *, bare_budgets: bool = False) -> int | None:
    t = unicodedata.normalize("NFD", text.lower())
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn").replace("đ", "d")
    # 1tr5 = 1.5 million; normalize removes punctuation but not letters.
    m = re.search(r"\b(\d{1,3})\s*tr\s*(\d{1,2})\b", t)
    if m:
        return int(m[1]) * 1_000_000 + int(m[2]) * (100_000 if len(m[2]) == 1 else 10_000)
    m = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(trieu|tr|cu|k|nghin|ngan|ngan dong|vnd|dong)\b", t)
    if m:
        v = float(m[1].replace(",", "."))
        suffix = m[2]
        scale = 1_000_000 if suffix in ("trieu", "tr", "cu") else 1_000 if suffix in ("k", "nghin", "ngan", "ngan dong") else 1
        return int(round(v * scale))
    m = re.search(r"\b\d{5,9}\b", t)
    if m:
        return int(m.group())
    if bare_budgets and re.fullmatch(r"\s*\d{2,4}\s*", t):
        return int(t) * 1000
    return None


def parse_budget(text: str, *, bare_budgets: bool = False) -> tuple[int | None, int | None]:
    q = unicodedata.normalize("NFD", text.lower())
    q = "".join(ch for ch in q if unicodedata.category(ch) != "Mn").replace("đ", "d")
    # Range written as "500k - 1tr" or "từ 500k đến 1 triệu".
    range_match = re.search(r"(\d+(?:[.,]\d+)?\s*(?:k|nghin|ngan|trieu|tr\d{1,2}|tr|cu))\s*(?:-|den|toi)\s*(\d+(?:[.,]\d+)?\s*(?:k|nghin|ngan|trieu|tr\d{1,2}|tr|cu))", q)
    if range_match:
        a, b = _parse_number(range_match[1]), _parse_number(range_match[2])
        if a and b and 50_000 <= min(a,b) <= max(a,b) <= 200_000_000:
            return min(a,b), max(a,b)
    number = _parse_number(text, bare_budgets=bare_budgets)
    if number and 50_000 <= number <= 200_000_000:
        return None, number
    return None, None


def detect_style(text: str) -> str:
    q = normalize(text)
    if any(s in q for s in ("chup tai", "over ear", "on ear", "headphone")):
        return "over_ear"
    if any(s in q for s in ("co day", "wired", "jack 3 5")):
        return "wired"
    if any(s in q for s in ("khong day", "nhet tai", "true wireless", "tws", "earbud", "in ear")):
        return "tws"
    if any(s in q for s in ("khong quan trong", "nao cung duoc", "tuy", "bat ky", "deu duoc")):
        return "any"
    return ""


def detect_usage(text: str) -> str:
    q = normalize(text)
    categories = [
        ("gaming", ("game", "gaming", "choi game")),
        ("calls", ("hop", "hoc online", "goi dien", "call", "meeting", "lam viec", "dam thoai")),
        ("sports", ("tap gym", "chay bo", "the thao", "tap luyen", "dap xe")),
        ("commute", ("di xe", "di duong", "xe buyt", "van phong on", "di hoc")),
        ("music", ("nhac", "nghe nhac", "podcast", "xem phim")),
    ]
    for category, keys in categories:
        if any(key in q for key in keys):
            return category
    if any(key in q for key in ("tat ca", "da nang", "khong quan trong", "binh thuong", "sao cung duoc")):
        return "any"
    return ""


def detect_priority(text: str) -> str:
    q = normalize(text)
    for key, signals in [
        ("anc", ("chong on", "anc", "on ao")),
        ("mic", ("mic", "micro", "dam thoai", "goi dien")),
        ("battery", ("pin", "lau", "pin trau")),
        ("sound", ("am thanh", "bass", "nhac hay", "chat am")),
        ("comfort", ("em tai", "nhe", "de chiu", "thoai mai")),
        ("price", ("gia re", "tiet kiem", "re nhat", "gia tot")),
    ]:
        if any(re.search(r"(?<!\w)" + re.escape(s) + r"(?!\w)", q) for s in signals):
            return key
    if any(s in q for s in ("khong quan trong", "bat ky", "nao cung duoc", "deu duoc", "tuy")):
        return "any"
    return ""


def detect_device(text: str, *, answering: bool = False) -> str:
    q = normalize(text)
    if any(k in q for k in ("iphone", "ipad", "ios", "macbook", "apple")):
        return "apple"
    if any(k in q for k in ("android", "samsung", "xiaomi", "oppo", "realme")):
        return "android"
    if any(k in q for k in ("laptop", "may tinh", "pc", "windows", "desktop")):
        return "computer"
    if any(k in q for k in ("nhieu thiet bi", "ca hai", "tat ca thiet bi", "da nen tang")):
        return "mixed"
    if answering and any(k in q for k in ("khong quan trong", "khong ro", "bo qua", "nao cung duoc", "deu duoc")):
        return "any"
    return ""


def _message_with_complete_urls(lines: list[str], *, max_chars: int = 1900) -> str:
    """Prevent slicing a retailer URL in half when Zalo messages approach the limit."""
    taken=[]
    for line in lines:
        candidate="\n".join([*taken,line])
        if len(candidate) > max_chars - 80:
            taken.append("(Còn các link khác: gõ 'gửi link' hoặc 'so sánh 1')")
            break
        taken.append(line)
    return "\n".join(taken)


def _valid_price(db: Session, listing: RetailerListing):
    latest = db.scalar(select(RetailerPriceSnapshot).where(RetailerPriceSnapshot.listing_id == listing.id).order_by(RetailerPriceSnapshot.observed_at.desc(), RetailerPriceSnapshot.id.desc()).limit(1))
    if latest is None:
        return None
    at = latest.observed_at.replace(tzinfo=timezone.utc) if latest.observed_at.tzinfo is None else latest.observed_at
    return latest if datetime.now(timezone.utc) - at <= timedelta(hours=24) else None


def _shortlist(db: Session, s: BuyerSession) -> AgentResult:
    stmt = select(BuyerProductSpec, CompareModel).join(CompareModel, BuyerProductSpec.model_key == CompareModel.model_key).where(BuyerProductSpec.category == s.category)
    data = db.execute(stmt).all()
    ranked = []
    for spec, model in data:
        if s.style and s.style != "any" and spec.style != s.style:
            continue
        # Conservative historical-market segment filter; not proof of current price.
        if s.budget_max and spec.reference_min_vnd and spec.reference_min_vnd > s.budget_max:
            continue
        listings = db.scalars(select(RetailerListing).where(RetailerListing.model_id == model.id).order_by(RetailerListing.retailer)).all()
        if not listings:
            continue
        available = [(l, snapshot) for l in listings if (snapshot := _valid_price(db, l)) is not None]
        prices = [p.price_vnd for _,p in available]
        # Don't pretend an unknown price is within budget. Include unpriced items as "verify first" candidates.
        valid = [p for p in prices if (s.budget_max is None or p <= s.budget_max) and (s.budget_min is None or p >= s.budget_min)]
        if prices and not valid:
            continue
        score = (3 if s.use_case in (spec.use_cases or []) else 0) + (4 if s.priority in (spec.tags or []) else 0)
        score += (1 if prices else 0)
        ranked.append((bool(valid), score, min(valid) if valid else 1_000_000_000, model, spec, listings, available))
    ranked.sort(key=lambda x: (-int(x[0]), -x[1], x[2], x[3].name))
    picks = ranked[:3]
    s.shortlisted_keys = [m.model_key for _,_,_,m,_,_,_ in picks]
    s.stage = "results" if picks else "no_match"
    db.add(s)
    db.commit()
    if not picks:
        return AgentResult("Chưa tìm được mẫu đã đối chiếu thông số/nguồn phù hợp. Bạn thử đổi kiểu tai nghe hoặc ngân sách bằng /tuvan.", "advisor_no_match", [])
    budget_label = money(s.budget_max) if s.budget_max else "chưa giới hạn"
    out = [f"🛍️ Gợi ý {s.category.replace('_',' ')} (ngân sách ≤ {budget_label})"]
    sources = []
    for index, (priced, score, cheapest, model, spec, listings, available) in enumerate(picks,1):
        out.append(f"\n{index}. {model.name}")
        if available and priced:
            first = min(((p.price_vnd,l) for l,p in available if s.budget_max is None or p.price_vnd <= s.budget_max), default=None, key=lambda v:v[0])
            if first:
                out.append(f"Giá quan sát ≤24h: từ {money(first[0])} ({RETAILERS[first[1].retailer][0]}); xác nhận lại khi mua")
        else:
            out.append("Giá: CHƯA XÁC MINH — không bảo đảm hợp ngân sách")
        if spec.highlights:
            out.append(f"Đặc điểm: {spec.highlights[:100]}")
        if not available and spec.reference_min_vnd and spec.reference_max_vnd:
            out.append("Phân khúc giá tham khảo (KHÔNG phải báo giá hiện tại): " + money(spec.reference_min_vnd) + "–" + money(spec.reference_max_vnd))
        out.append("Nguồn: " + ", ".join(RETAILERS[l.retailer][0] for l in listings[:3]))
        for l in listings[:2]:
            out.append(l.url)
            sources.append(l.url)
    if s.device and s.device not in ("any", ""):
        out.append("\nThiết bị: " + s.device + ". Kiểm tra hỗ trợ Bluetooth/codec và độ trễ theo từng trang sản phẩm trước khi đặt mua.")
    out.append("\nGõ 'so sánh 1', 'so sánh 2', 'loại nào rẻ nhất', 'gửi link', hoặc 'đổi ngân sách 700k'.")
    return AgentResult(_message_with_complete_urls(out), "advisor_recommend", sources)


def _wants_best_choice(q: str) -> bool:
    """Recognize natural Vietnamese best-value/which-one follow-ups."""
    triggers = (
        "dang mua nhat", "nen mua mau nao", "nen chon mau nao",
        "nen mua cai nao", "nen chon cai nao", "mau nao tot nhat",
        "mau nao phu hop nhat", "mau nao hop nhat", "chot mau nao",
        "loai nao dang mua", "cai nao dang mua", "nen lay mau nao",
        "trong 3 mau do", "trong ba mau do", "trong cac mau do",
    )
    return any(t in q for t in triggers)


def _best_for_session(db: Session, session: BuyerSession) -> AgentResult:
    """Recommend from the *actual* stored shortlist, never manufacture a product or price.

    Prefer feature/usage match. Current price is only asserted if a <=24h verified
    snapshot exists; 'reference_min' is a historical segment hint, not an offer.
    """
    keys = (session.shortlisted_keys or [])[:3]
    if not keys:
        return AgentResult(
            "Mình chưa có danh sách 3 mẫu thực sự phù hợp trong cuộc tư vấn này, "
            "nên chưa thể chọn trung thực mẫu đáng mua nhất. "
            "Bạn có thể thử 'gợi ý lại', 'đổi ngân sách 1 triệu' hoặc /tuvan.",
            "advisor_no_shortlist", [],
        )
    candidates = []
    for index, key in enumerate(keys, 1):
        model = db.scalar(select(CompareModel).where(CompareModel.model_key == key))
        spec = db.scalar(select(BuyerProductSpec).where(BuyerProductSpec.model_key == key))
        if model is None or spec is None or spec.category != session.category:
            continue
        listings = db.scalars(select(RetailerListing).where(RetailerListing.model_id == model.id).order_by(RetailerListing.retailer)).all()
        if not listings:
            continue
        offers = []
        for listing in listings:
            snap = _valid_price(db, listing)
            if snap is not None:
                offers.append((snap.price_vnd, listing, snap))
        offers.sort(key=lambda v: v[0])
        valid = [x for x in offers if
                 (session.budget_max is None or x[0] <= session.budget_max) and
                 (session.budget_min is None or x[0] >= session.budget_min)]
        # A known, freshly confirmed price over budget is not a feasible offer.
        if offers and not valid:
            continue
        match_priority = session.priority and session.priority in (spec.tags or [])
        match_usage = session.use_case and session.use_case in (spec.use_cases or [])
        # A suitable feature matters more than a price difference, especially for ANC.
        relevance = 5 * bool(match_priority) + 3 * bool(match_usage)
        if session.style and session.style != "any" and spec.style != session.style:
            continue
        score = relevance + (1 if valid else 0)
        candidates.append((score, bool(valid), valid[0][0] if valid else 10**12,
                           index, model, spec, listings, valid))
    if not candidates:
        return AgentResult(
            "Các mẫu vừa gợi ý không còn đủ căn cứ về phiên bản/giá trong ngân sách. "
            "Mình chưa thể chọn mẫu đáng mua nhất; hãy đổi ngân sách hoặc kiểm tra link sản phẩm.",
            "advisor_no_feasible_choice", [],
        )
    candidates.sort(key=lambda x: (-x[0], -int(x[1]), x[2], x[4].name))
    score, verified, price, index, best, spec, listings, valid = candidates[0]
    out = [f"🏆 Theo nhu cầu đã ghi nhận, mình ưu tiên {best.name} (mẫu {index}).", "Vì:"]
    if session.style and session.style != "any":
        style_names = {"tws": "nhét tai Bluetooth TWS", "over_ear": "chụp tai", "wired": "có dây"}
        out.append("• Đúng kiểu " + style_names.get(session.style, session.style) + " bạn đã chọn.")
    priority_names = {"anc": "chống ồn ANC", "battery": "pin lâu", "sound": "âm thanh/bass", "mic": "micro/đàm thoại", "comfort": "thoải mái", "price": "tiết kiệm"}
    if session.priority and session.priority != "any":
        if session.priority in (spec.tags or []):
            out.append("• Có đặc điểm ưu tiên: " + priority_names.get(session.priority, session.priority) + " theo thông tin đã lưu.")
        else:
            out.append("• Chưa có thông số được kiểm chứng cho ưu tiên " + priority_names.get(session.priority, session.priority) + ".")
    usage_names = {"music": "nghe nhạc", "calls": "học/họp, gọi điện", "sports": "thể thao", "gaming": "chơi game", "commute": "di chuyển"}
    if session.use_case and session.use_case in (spec.use_cases or []):
        out.append("• Phù hợp nhu cầu " + usage_names.get(session.use_case, session.use_case) + " theo mô tả nguồn.")
    if spec.highlights:
        out.append("• Điểm đáng chú ý: " + spec.highlights[:190])
    if verified:
        amount, listing, snap = valid[0]
        out.append(f"• Giá quan sát được trong 24 giờ: {money(amount)} tại {RETAILERS[listing.retailer][0]} (hãy kiểm tra lại trước khi đặt).")
        top_url = listing.url
    else:
        out.append("• CHƯA CÓ GIÁ ĐƯỢC XÁC MINH TRONG 24 GIỜ: chưa thể khẳng định rẻ nhất hoặc chắc chắn nằm trong ngân sách.")
        if spec.reference_min_vnd and spec.reference_max_vnd:
            out.append(f"• Phân khúc tham khảo, KHÔNG phải báo giá: {money(spec.reference_min_vnd)}–{money(spec.reference_max_vnd)}.")
        top_url = spec.evidence_url if spec.evidence_url in {l.url for l in listings} else listings[0].url
    other_names = [c[4].name for c in sorted(candidates, key=lambda x: x[3]) if c[4].model_key != best.model_key]
    if other_names:
        out.append("So với các lựa chọn khác (" + ", ".join(other_names[:2]) + "), mẫu này khớp nhiều tiêu chí đã xác minh hơn; không kết luận chất lượng tổng thể hơn khi thiếu phép đo thực tế.")
    if session.device in ("apple", "android", "computer", "mixed"):
        out.append("Lưu ý: kiểm tra codec/kết nối với thiết bị của bạn trên trang hãng trước khi chốt.")
    out.append("🔗 Trang sản phẩm để tự xem giá/đặt mua:")
    urls = [top_url]
    out.append(top_url)
    for listing in listings:
        if listing.url != top_url:
            urls.append(listing.url)
            out.append(f"{RETAILERS[listing.retailer][0]}: {listing.url}")
            break
    out.append("Nếu muốn, gõ 'so sánh 1', 'gửi link' hoặc 'đổi ngân sách 700k'.")
    return AgentResult(_message_with_complete_urls(out), "advisor_best_choice", urls)


def _session(db:Session, chat_id:str) -> BuyerSession:
    s = db.get(BuyerSession, chat_id)
    if s is None:
        s = BuyerSession(chat_id=chat_id)
        db.add(s)
        db.flush()
    return s


def _ask_next(s:BuyerSession) -> AgentResult:
    if s.budget_max is None:
        s.stage = "budget"
        return AgentResult("Bạn dự trù ngân sách bao nhiêu? VD: 500k, dưới 1 triệu, 500k đến 1tr5. Mình sẽ so giá theo cùng mã sản phẩm.", "advisor_budget", [])
    if s.category == "tai_nghe" and not s.style:
        s.stage = "style"
        return AgentResult("Bạn thích tai nghe kiểu nào? 1) Nhét tai Bluetooth (TWS), 2) Chụp tai, 3) Có dây, hoặc 'không quan trọng'?", "advisor_style", [])
    if s.category == "tai_nghe" and not s.use_case:
        s.stage = "usage"
        return AgentResult("Bạn dùng chủ yếu làm gì? Nghe nhạc, học/họp online, chơi game, chạy bộ hay đi đường?", "advisor_usage", [])
    if s.category == "tai_nghe" and not s.priority:
        s.stage = "priority"
        return AgentResult("Bạn ưu tiên điều gì nhất: pin lâu, mic rõ, chống ồn, chất âm, êm tai hay giá rẻ? (Có thể nói 'không quan trọng')", "advisor_priority", [])
    if s.category == "tai_nghe" and not s.device:
        s.stage = "device"
        return AgentResult("Bạn sẽ dùng tai nghe với thiết bị nào: iPhone/iPad, Android, laptop/PC hay nhiều thiết bị? (Có thể nói 'bỏ qua')", "advisor_device", [])
    return AgentResult("", "advisor_ready", [])


def _parse_style_answer(text:str, stage:str) -> str:
    normalized = normalize(text).strip()
    if stage == "style":
        if normalized in ("1", "mot"):
            return "tws"
        if normalized in ("2", "hai"):
            return "over_ear"
        if normalized in ("3", "ba"):
            return "wired"
    return detect_style(text)


def wants_advice(db:Session, chat_id:str, question:str) -> bool:
    raw = question.strip()
    normalized = normalize(raw)
    if raw.startswith("/"):
        return raw.lower().startswith(("/tuvan", "/goiy"))
    s = db.get(BuyerSession, chat_id)
    if s and s.stage in ("category", "budget", "style", "usage", "priority", "device", "results", "no_match"):
        # A new support/policy intent belongs to normal v3 graph.
        if any(k in normalized for k in ("bao hanh", "doi tra", "hoan tien", "don hang", "van chuyen", "giao hang")):
            return False
        return True
    triggers = ("muon mua", "can mua", "tu van mua", "tu van", "tim mua", "chon mua", "goi y mua", "mua tai nghe", "mua dien thoai")
    return any(k in normalized for k in triggers)


def advise(db: Session, chat_id: str, question: str) -> AgentResult:
    q = normalize(question).strip()
    raw = question.strip()
    if q in ("tuvan", "goiy", "bat dau lai", "tu van lai", "reset tu van", "mua cai khac"):
        old = db.get(BuyerSession, chat_id)
        if old:
            db.delete(old)
            db.commit()
        db.add(BuyerSession(chat_id=chat_id, stage="category"))
        db.commit()
        return AgentResult("Bạn muốn mua sản phẩm gì? Tai nghe, điện thoại, sạc, pin dự phòng, chuột…", "advisor_start", [])

    s = _session(db, chat_id)
    cat = detect_category(raw)
    if not s.category:
        if not cat:
            return AgentResult("Bạn định mua loại nào: tai nghe, điện thoại, sạc hay pin dự phòng?", "advisor_category", [])
        s.category = cat
        s.stage = "budget"
    elif cat and cat != s.category and any(k in q for k in ("muon mua", "can mua", "tu van mua", "doi sang")):
        s.category, s.budget_min, s.budget_max, s.style, s.use_case, s.priority, s.device = cat,None,None,"","","",""
        s.stage = "budget"

    # A previous "no match" is NOT a 3-product recommendation. Do not invent one.
    if s.stage == "no_match":
        low, high = parse_budget(raw, bare_budgets=True)
        if high:
            s.budget_min, s.budget_max = low, high
            db.commit()
            return _shortlist(db, s)
        if _wants_best_choice(q):
            return _best_for_session(db, s)
        if any(key in q for key in ("goi y", "tim lai", "thu lai", "de xuat")):
            return _shortlist(db, s)  # recheck after new catalog data loaded
        if any(key in q for key in ("doi kieu", "doi nhu cau", "doi uu tien")):
            s.style, s.use_case, s.priority, s.device = "", "", "", ""
            db.commit()
            return _ask_next(s)
        return AgentResult("Mình chưa tìm được mẫu phù hợp từ dữ liệu đã có. "
                           "Bạn thử 'gợi ý lại', 'đổi ngân sách 700k' hoặc /tuvan nhé.",
                           "advisor_no_match_followup", [])
    if s.stage == "results":
        if _wants_best_choice(q):
            return _best_for_session(db, s)
        if any(k in q for k in ("goi y", "de xuat", "chon 3 mau", "tim lai")):
            return _shortlist(db, s)
        if any(k in q for k in ("so sanh", "link", "gia o", "gia ben")) and s.shortlisted_keys:
            n = re.search(r"\b([1-3])\b",q)
            if n:
                idx=int(n[1])-1
                if idx >= len(s.shortlisted_keys):
                    return AgentResult("Danh sách hiện tại không có lựa chọn đó.", "advisor_bad_choice", [])
                return compare_price(db, s.shortlisted_keys[idx])
            if "link" in q:
                sources = []
                for key in s.shortlisted_keys[:3]:
                    model=db.scalar(select(CompareModel).where(CompareModel.model_key==key))
                    if model:
                        links=db.scalars(select(RetailerListing).where(RetailerListing.model_id==model.id)).all()
                        sources.extend([f"{model.name}: {l.url}" for l in links[:2]])
                return AgentResult(_message_with_complete_urls(["🔗 Link tham khảo:", *sources]), "advisor_links", [u for u in s.shortlisted_keys])
            if "so sanh" in q:
                return compare_price(db, s.shortlisted_keys[0])
        if any(x in q for x in ("re nhat", "gia tot nhat")):
            candidates = []
            for key in s.shortlisted_keys:
                model = db.scalar(select(CompareModel).where(CompareModel.model_key == key))
                if model:
                    for listing in db.scalars(select(RetailerListing).where(RetailerListing.model_id == model.id)):
                        snap = _valid_price(db, listing)
                        if snap:
                            candidates.append((snap.price_vnd, model.name, listing.url))
            if not candidates:
                return AgentResult("Chưa có giá được xác minh trong 24h nên mình chưa thể khẳng định mẫu nào rẻ nhất. Bạn mở các link bên trên để kiểm tra nhé.", "advisor_unverified", [])
            p, name, url = min(candidates)
            return AgentResult(f"Trong giá xác minh ≤24h: {name} — {money(p)}. Kiểm tra lại phí ship và khuyến mãi:\n{url}", "advisor_lowest", [url])
        if "ngan sach" in q or "duoi" in q or "trieu" in q or re.search(r"\d+k\b",q):
            low, high=parse_budget(raw, bare_budgets=True)
            if high:
                s.budget_min,s.budget_max=low,high
                db.commit()
                return _shortlist(db,s)
        if any(key in q for key in ("doi kieu", "doi nhu cau", "doi uu tien")):
            s.style,s.use_case,s.priority,s.device="","","",""
            db.commit()
        else:
            return AgentResult("Bạn có thể hỏi 'so sánh 1', 'gửi link', 'loại nào rẻ nhất', 'đổi ngân sách 700k', hoặc '/tuvan' để làm lại.", "advisor_followup", [])

    low, high = parse_budget(raw, bare_budgets=s.stage == "budget")
    if high:
        s.budget_min,s.budget_max = low,high
    if s.category == "tai_nghe":
        v=_parse_style_answer(raw, s.stage)
        if v:
            s.style=v
        v=detect_usage(raw)
        if v:
            s.use_case=v
        v=detect_priority(raw)
        if v:
            s.priority=v
        v=detect_device(raw, answering=s.stage == "device")
        if v:
            s.device=v
    next_question = _ask_next(s)
    db.add(s)
    db.commit()
    if next_question.intent != "advisor_ready":
        return next_question
    return _shortlist(db, s)
