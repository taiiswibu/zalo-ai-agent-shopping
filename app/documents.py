"""Ingest trusted local policy documents as review-required RAG sources."""
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Knowledge


def ingest_file(db: Session, file: Path) -> str:
    if file.suffix.lower() in {".md", ".txt"}:
        text = file.read_text(encoding="utf-8")
    elif file.suffix.lower() == ".pdf":
        try:
            import fitz
        except ImportError as exc:
            raise RuntimeError("pip install -r requirements-docs.txt để xử lý PDF") from exc
        with fitz.open(file) as pdf:
            if len(pdf) > 50:
                raise ValueError("Tài liệu dài hơn 50 trang; chia nhỏ trước khi nạp")
            text = "\n".join(page.get_text() for page in pdf)
    else:
        raise ValueError("Chỉ hỗ trợ .md/.txt/.pdf")
    if not text.strip():
        raise ValueError("Tài liệu không có văn bản; PDF scan cần OCR riêng")
    import hashlib
    key = "file:" + hashlib.sha256(str(file.resolve()).encode()).hexdigest()[:24]
    doc = db.scalar(select(Knowledge).where(Knowledge.source_key == key))
    if not doc:
        doc = Knowledge(source_key=key, title=file.name, content=text[:14000], source_url="file://" + file.name, approved=False)
        db.add(doc)
    else:
        if doc.content != text[:14000]:
            doc.approved = False
        doc.content = text[:14000]
    db.commit()
    return key
