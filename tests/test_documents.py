from pathlib import Path
from sqlalchemy import select
from app.documents import ingest_file
from app.models import Knowledge


def test_document_requires_review(db, tmp_path):
    doc = tmp_path / "policy.md"
    doc.write_text("# Warranty\nChính sách đã soạn nhưng chưa duyệt", encoding="utf-8")
    key = ingest_file(db, doc)
    row = db.scalar(select(Knowledge).where(Knowledge.source_key == key))
    assert not row.approved
    row.approved = True
    db.commit()
    doc.write_text("# Warranty\nChính sách đã cập nhật", encoding="utf-8")
    ingest_file(db, doc)
    assert not row.approved
